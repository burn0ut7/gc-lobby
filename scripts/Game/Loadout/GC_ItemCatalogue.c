//! Derived, session-local metadata. No live entities or retained Resource handles.
class GC_ItemCatalogueEntry
{
	ResourceName m_sPrefab;
	string m_sName;
	string m_sNameKey;
	//! Addon root where resource discovery first found this identity, not override precedence.
	string m_sSource;
	ref array<string> m_aSourceAddons = {};
	string m_sCategory;
	bool m_bPortable;
	string m_sEquipmentSlot;
	string m_sSearch;
	string m_sFactions;
	typename m_AttachmentType;
	typename m_MagazineWell;
	ref array<string> m_aMountNames = {};
	ref array<typename> m_aMountTypes = {};

	//------------------------------------------------------------------------------------------------
	string GetSortKey(bool descending)
	{
		string name = m_sName.Trim();
		name.ToLower();
		bool symbols = name.IsEmpty();
		if (!symbols)
			symbols = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~".Contains(name.Substring(0, 1));
		// Keep punctuation-prefixed names last in both sort directions.
		string group = "1";
		if (symbols == descending)
			group = "0";
		return group + name + "\t" + m_sPrefab;
	}

	//------------------------------------------------------------------------------------------------
	string GetCategoryLabel()
	{
		string label = m_sCategory;
		if (m_sEquipmentSlot.StartsWith("Loadout"))
		{
			label = m_sEquipmentSlot;
			label.Replace("Loadout", "");
			label.Replace("Area", "");
			label.Replace("HeadCover", "Headwear");
		}
		return "#GC-Catalogue_" + label;
	}
}

//! Shared resource discovery and metadata indexing; caller owns lifetime and frame ticks.
class GC_ItemCatalogue
{
	protected ref GC_ItemCatalogue m_VehicleCatalogue;

	//------------------------------------------------------------------------------------------------
	GC_ItemCatalogue GetVehicles()
	{
		if (!m_VehicleCatalogue)
			m_VehicleCatalogue = new GC_ItemCatalogue();
		return m_VehicleCatalogue;
	}

	protected ref array<ResourceName> m_aPending = {};
	protected ref array<string> m_aSources = {};
	protected ref map<ResourceName, bool> m_mSeen = new map<ResourceName, bool>();
	protected ref array<ref GC_ItemCatalogueEntry> m_aItems = {};
	protected ref array<string> m_aAddons = {};
	protected ref array<SCR_EntityCatalog> m_aFactionCatalogues = {};
	protected ref array<string> m_aFactionKeys = {};
	protected string m_sDiscovering;
	protected int m_iAddon;
	protected int m_iProcessed;
	protected int m_iFailed;
	protected int m_iExcluded;
	protected float m_fMaxTickMS;
	protected float m_fStartedMS;
	protected float m_fElapsedMS;
	protected bool m_bStarted;
	protected bool m_bDiscoveryFailed;
	protected ref array<string> m_aDiscoveryIssues = {};
	protected ref array<ResourceName> m_aFailedResources = {};
	protected static const int INDEX_BATCH_SIZE = 4096;
	protected static const float INDEX_BUDGET_MS = 2;
	protected float m_fBudgetMS = INDEX_BUDGET_MS;
	protected float m_fLastTickMS;
	protected static int s_iAuthoredRevision;
	protected int m_iAuthoredRevision;
	protected ref GC_ItemCatalogueQuery m_CachedQuery;
	protected int m_iSearchIndexed;
	protected ref array<string> m_aSearchTerms = {};
	protected ref array<GC_ItemCatalogueEntry> m_aSearchMatches = {};
	protected ref array<string> m_aSearchKeys = {};
	protected ref map<string, GC_ItemCatalogueEntry> m_mSearchEntries = new map<string, GC_ItemCatalogueEntry>();

	//------------------------------------------------------------------------------------------------
	static void NotifyAuthoredSave()
	{
		s_iAuthoredRevision++;
	}

	//------------------------------------------------------------------------------------------------
	bool HasAuthoredChanges()
	{
		return m_iAuthoredRevision != s_iAuthoredRevision;
	}

	//------------------------------------------------------------------------------------------------
	void TickFrame(float timeSlice)
	{
		// Estimate non-index work from the previous frame. Target at most
		// 10 ms, and fall back to 1 ms when the frame has no spare time at 60 Hz.
		float otherWorkMS = Math.Max(0, timeSlice * 1000 - m_fLastTickMS);
		m_fBudgetMS = Math.Clamp(16 - otherWorkMS, 1, 10);
		float started = System.GetTickCount();
		Tick();
		m_fLastTickMS = System.GetTickCount() - started;
	}

	//------------------------------------------------------------------------------------------------
	void Start()
	{
		if (m_bStarted)
			return;
		m_bStarted = true;
		m_fStartedMS = System.GetTickCount();
		m_aAddons = SCR_AddonTool.GetAllAddonIDs();
		// Newly authored files may not yet be enumerated by the Play-time database.
		// Resolve their native meta identities, then use the same metadata queue.
#ifdef WORKBENCH
		QueueAuthoredWeapons();
#endif
		FactionManager manager = GetGame().GetFactionManager();
		if (!manager)
			return;
		array<Faction> factions = {};
		manager.GetFactionsList(factions);
		foreach (Faction faction : factions)
		{
			SCR_Faction gameFaction = SCR_Faction.Cast(faction);
			if (!gameFaction)
				continue;
			SCR_EntityCatalog catalogue = gameFaction.GetFactionEntityCatalogOfType(EEntityCatalogType.ITEM, false);
			if (!catalogue)
				continue;
			m_aFactionCatalogues.Insert(catalogue);
			m_aFactionKeys.Insert(faction.GetFactionKey());
		}
	}

	//------------------------------------------------------------------------------------------------
	void Tick()
	{
#ifdef WORKBENCH
		if (m_bStarted && m_iAuthoredRevision != s_iAuthoredRevision)
			QueueAuthoredWeapons();
#endif
		if (!m_bStarted || IsFinished())
			return;
		float started = System.GetTickCount();
		// Drain each discovered addon before searching the next one, so usable
		// entries reach the browser before discovery of the whole mod pack finishes.
		for (int batch; batch < INDEX_BATCH_SIZE && !IsFinished(); batch++)
		{
			if (m_iProcessed >= m_aPending.Count() && m_iAddon < m_aAddons.Count())
			{
				m_sDiscovering = m_aAddons[m_iAddon++];
				SearchResourcesFilter filter = new SearchResourcesFilter();
				filter.rootPath = "$" + m_sDiscovering + ":";
				filter.fileExtensions = {"et"};
				if (!ResourceDatabase.SearchResources(filter, OnResource))
				{
					m_bDiscoveryFailed = true;
					m_aDiscoveryIssues.Insert(m_sDiscovering);
				}
			}
			else if (m_iProcessed < m_aPending.Count())
			{
				ReadEntry(m_aPending[m_iProcessed], m_aSources[m_iProcessed]);
				m_iProcessed++;
			}
			// Native discovery and Resource.Load cannot be interrupted. Check after
			// either operation, including empty addons, without an extra idle frame.
			if (System.GetTickCount() - started >= m_fBudgetMS)
				break;
		}
		m_fMaxTickMS = Math.Max(m_fMaxTickMS, System.GetTickCount() - started);
		m_fElapsedMS = System.GetTickCount() - m_fStartedMS;
	}

	//------------------------------------------------------------------------------------------------
	protected void OnResource(ResourceName resourceName, string exactPath)
	{
		if (m_mSeen.Contains(resourceName))
			return;
		m_mSeen.Insert(resourceName, true);
		m_aPending.Insert(resourceName);
		m_aSources.Insert(m_sDiscovering);
	}

#ifdef WORKBENCH
	//------------------------------------------------------------------------------------------------
	protected void QueueAuthoredWeapons()
	{
		m_iAuthoredRevision = s_iAuthoredRevision;
		string directory;
		if (Workbench.GetAbsolutePath("$GlobalConflictsUnitsDev:Prefabs/Loadouts/", directory, false))
			FileIO.FindFiles(OnAuthoredWeapon, directory, ".et");
	}

	//------------------------------------------------------------------------------------------------
	protected void OnAuthoredWeapon(string filename)
	{
		ResourceManager manager = Workbench.GetModule(ResourceManager);
		MetaFile meta = manager.GetMetaFile(filename);
		if (!meta)
			return;
		ResourceName prefab = meta.GetResourceID();
		if (!prefab.GetPath().Contains("/Weapons/"))
			return;
		m_sDiscovering = "GlobalConflictsUnitsDev";
		OnResource(prefab, filename);
	}
#endif

	//------------------------------------------------------------------------------------------------
	static GC_ItemCatalogueEntry ReadResource(ResourceName prefab)
	{
		GC_ItemCatalogue catalogue = new GC_ItemCatalogue();
		catalogue.ReadEntry(prefab, "");
		if (catalogue.Count() == 0)
			return null;
		return catalogue.Get(0);
	}

	//------------------------------------------------------------------------------------------------
	protected void ReadEntry(ResourceName prefab, string source)
	{
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
		{
			m_iFailed++;
			m_aFailedResources.Insert(prefab);
			return;
		}
		IEntitySource entity = SCR_BaseContainerTools.FindEntitySource(resource);
		if (entity && m_bStarted)
		{
			typename rootType = entity.GetClassName().ToType();
			if (rootType && rootType.IsInherited(Vehicle))
			{
				GC_VehicleCatalogueEntry vehicle = GC_VehicleCatalogueEntry.Read(prefab, resource, entity);
				if (vehicle)
					GetVehicles().m_aItems.Insert(vehicle);
			}
		}
		IEntityComponentSource inventory = SCR_ComponentHelper.GetInventoryItemComponentSource(entity);
		if (!inventory)
		{
			m_iExcluded++;
			return;
		}
		GC_ItemCatalogueEntry entry = new GC_ItemCatalogueEntry();
		entry.m_sPrefab = prefab;
		entry.m_sSource = source;
		entity.GetSourceAddons(entry.m_aSourceAddons);
		entry.m_sName = FilePath.StripExtension(FilePath.StripPath(prefab));
		entry.m_sCategory = "Equipment";
		// Resolve the first matching components in one pass. Repeated helper
		// searches traverse and convert the same component sources for every item.
		IEntityComponentSource mesh;
		IEntityComponentSource weapon;
		IEntityComponentSource magazine;
		IEntityComponentSource cloth;
		IEntityComponentSource storage;
		array<IEntityComponentSource> mounts = {};
		array<IEntityComponentSource> children = {};
		array<IEntityComponentSource> grandchildren = {};
		for (int c, componentCount = entity.GetComponentCount(); c < componentCount; c++)
		{
			IEntityComponentSource component = entity.GetComponent(c);
			if (!component)
				continue;
			string className = component.GetClassName();
			if (!mesh && className == "MeshObject")
				mesh = component;
			typename componentType = className.ToType();
			if (!componentType)
				continue;
			if (!weapon && componentType.IsInherited(WeaponComponent))
				weapon = component;
			if (!magazine && componentType.IsInherited(BaseMagazineComponent))
				magazine = component;
			if (!cloth && componentType.IsInherited(BaseLoadoutClothComponent))
				cloth = component;
			if (!storage && componentType.IsInherited(BaseInventoryStorageComponent))
				storage = component;
			if (componentType.IsInherited(AttachmentSlotComponent))
				mounts.Insert(component);
			// Match the native helper's root/child/grandchild order, but reuse
			// scratch arrays instead of allocating two arrays for every component.
			SCR_BaseContainerTools.GetComponentSourceChildren(component, children);
			foreach (IEntityComponentSource child : children)
			{
				typename childType = child.GetClassName().ToType();
				if (childType && childType.IsInherited(AttachmentSlotComponent))
					mounts.Insert(child);
				SCR_BaseContainerTools.GetComponentSourceChildren(child, grandchildren);
				foreach (IEntityComponentSource grandchild : grandchildren)
				{
					typename grandchildType = grandchild.GetClassName().ToType();
					if (grandchildType && grandchildType.IsInherited(AttachmentSlotComponent))
						mounts.Insert(grandchild);
				}
			}
		}
		SCR_ItemAttributeCollection attributes = SCR_ComponentHelper.GetInventoryItemInfo(inventory);
		if (attributes)
		{
			// Match native inventory drag visibility, excluding actors/vehicles that merely own storage.
			typename entityType = entity.GetClassName().ToType();
			BaseContainer attributeSource = inventory.GetObject("Attributes");
			bool visible = true;
			attributeSource.Get("m_bVisible", visible);
			bool enabled = true;
			inventory.Get("Enabled", enabled);
			// Storage interfaces also have display/drag attributes. Carryable gear
			// needs item physics; loot-storage markers represent the world container.
			ItemPhysicalAttributes physical = ItemPhysicalAttributes.Cast(attributes.FindAttribute(ItemPhysicalAttributes));
			entry.m_bPortable = attributes.IsDraggable() && visible && enabled && physical && attributes.GetSlotType() != ESlotID.SLOT_LOOT_STORAGE;
			// Empty or placeholder models are not finished inventory gear, even when named otherwise.
			ResourceName model;
			if (mesh)
				mesh.Get("Object", model);
			string modelPath = model;
			modelPath.ToLower();
			if (model.IsEmpty() || modelPath.EndsWith("common/models/default.xob"))
				entry.m_bPortable = false;
			if (entityType && (entityType.IsInherited(ChimeraCharacter) || entityType.IsInherited(Vehicle)))
				entry.m_bPortable = false;
			UIInfo info = attributes.GetUIInfo();
			if (info && !info.GetName().IsEmpty())
			{
				entry.m_sNameKey = info.GetName();
				string translated = WidgetManager.Translate(info.GetName());
				if (!translated.IsEmpty() && !translated.StartsWith("#"))
					entry.m_sName = translated;
			}
			else
				entry.m_bPortable = false;
			WeaponAttachmentAttributes attachment = WeaponAttachmentAttributes.Cast(attributes.FindAttribute(WeaponAttachmentAttributes));
			if (attachment && attachment.GetAttachmentType())
			{
				entry.m_AttachmentType = attachment.GetAttachmentType().Type();
				entry.m_sCategory = "Attachments";
			}
		}
		if (weapon)
		{
			entry.m_sCategory = "Weapons";
			weapon.Get("WeaponSlotType", entry.m_sEquipmentSlot);
			if (entry.m_sEquipmentSlot == "grenade" || entry.m_sEquipmentSlot == "throwable")
				entry.m_sCategory = "Grenades";
		}
		if (magazine)
		{
			entry.m_sCategory = "Magazines";
			BaseContainer well = magazine.GetObject("MagazineWell");
			if (well)
				entry.m_MagazineWell = well.GetClassName().ToType();
		}
		bool clothEnabled = true;
		if (cloth)
			cloth.Get("Enabled", clothEnabled);
		if (cloth && clothEnabled)
		{
			entry.m_sCategory = "Equipment";
			// Wearable pouches can have storage without a standard body slot.
			// Dedicated headwear, vest and backpack categories still take precedence.
			if (storage)
				entry.m_sCategory = "Clothing";
			BaseContainer area = cloth.GetObject("AreaType");
			if (area)
			{
				entry.m_sEquipmentSlot = area.GetClassName();
				typename areaType = entry.m_sEquipmentSlot.ToType();
				// Categorize by the actual worn slot, including modded subclasses.
				if (areaType && (areaType.IsInherited(LoadoutJacketArea) || areaType.IsInherited(LoadoutPantsArea) || areaType.IsInherited(LoadoutHandwearSlotArea) || areaType.IsInherited(LoadoutBootsArea)))
					entry.m_sCategory = "Clothing";
				else if (areaType && areaType.IsInherited(LoadoutHeadCoverArea))
					entry.m_sCategory = "Headwear";
				else if (areaType && (areaType.IsInherited(LoadoutVestArea) || areaType.IsInherited(LoadoutArmoredVestSlotArea)))
					entry.m_sCategory = "Vests";
				else if (areaType && areaType.IsInherited(LoadoutBackpackArea))
					entry.m_sCategory = "Backpacks";
			}
		}
		foreach (IEntityComponentSource mount : mounts)
		{
			BaseContainer slot = mount.GetObject("AttachmentSlot");
			BaseContainer type = mount.GetObject("AttachmentType");
			bool enabled = true;
			if (slot)
				slot.Get("Enabled", enabled);
			if (!slot || !type || !enabled)
				continue;
			entry.m_aMountNames.Insert(slot.GetName());
			entry.m_aMountTypes.Insert(type.GetClassName().ToType());
		}
		for (int f; f < m_aFactionCatalogues.Count(); f++)
		{
			if (m_aFactionCatalogues[f].GetEntryWithPrefab(prefab))
				entry.m_sFactions += " " + m_aFactionKeys[f] + " ";
		}
		entry.m_sName.TrimInPlace();
		entry.m_sSearch = entry.m_sName + " " + entry.m_sNameKey + " " + prefab + " " + source + " " + entry.m_sCategory + " " + entry.m_sEquipmentSlot + " " + entry.m_sFactions;
		entry.m_sSearch += " " + WidgetManager.Translate(entry.GetCategoryLabel());
		foreach (string addon : entry.m_aSourceAddons)
			entry.m_sSearch += " " + addon;
		entry.m_sSearch.ToLower();
		m_aItems.Insert(entry);
	}

	//------------------------------------------------------------------------------------------------
	int Count()
	{
		return m_aItems.Count();
	}

	//------------------------------------------------------------------------------------------------
	GC_ItemCatalogueEntry Get(int index)
	{
		if (!m_aItems.IsIndexValid(index))
			return null;
		return m_aItems[index];
	}

	//------------------------------------------------------------------------------------------------
	array<string> GetAddons()
	{
		return m_aAddons;
	}

	//------------------------------------------------------------------------------------------------
	array<string> GetFactionKeys()
	{
		return m_aFactionKeys;
	}

	//------------------------------------------------------------------------------------------------
	//! Finished processing does not imply complete coverage. Inspect CoverageReport as well.
	bool IsFinished()
	{
		return m_bStarted && m_iAddon >= m_aAddons.Count() && m_iProcessed >= m_aPending.Count();
	}

	//------------------------------------------------------------------------------------------------
	bool HasCompleteCoverage()
	{
		return IsFinished() && !m_bDiscoveryFailed && m_iFailed == 0;
	}

	//------------------------------------------------------------------------------------------------
	string Status()
	{
		if (!m_bStarted)
			return "Not started";
		string state = "Indexing";
		if (IsFinished())
			state = "Indexed";
		if (m_bDiscoveryFailed)
			state = "Incomplete discovery";
		return string.Format("%1: %2 / %3 prefabs | %4 items | %5 non-items | %6 unreadable", state, m_iProcessed, m_aPending.Count(), Count(), m_iExcluded, m_iFailed);
	}

	//------------------------------------------------------------------------------------------------
	string CoverageReport()
	{
		string report = string.Format("finished=%1 coverageComplete=%2 observedFactionCatalogues=%3", IsFinished(), HasCompleteCoverage(), m_aFactionCatalogues.Count());
		foreach (string addon : m_aDiscoveryIssues)
			report += "\nResource search returned false: " + addon;
		foreach (ResourceName prefab : m_aFailedResources)
			report += "\nUnreadable: " + prefab;
		return report;
	}

	//------------------------------------------------------------------------------------------------
	string Metrics()
	{
		return Status() + string.Format(" | batch=%1 budgetMS=%2 maxIndexTickMS=%3 elapsedMS=%4", INDEX_BATCH_SIZE, m_fBudgetMS, m_fMaxTickMS, m_fElapsedMS);
	}

	//------------------------------------------------------------------------------------------------
	bool Matches(int index, array<string> terms, string category, string equipmentSlot, string source, string faction, typename mountType)
	{
		if (!m_aItems.IsIndexValid(index) || !terms)
			return false;
		GC_ItemCatalogueEntry entry = m_aItems[index];
		if (!category.IsEmpty() && entry.m_sCategory != category)
			return false;
		if (!equipmentSlot.IsEmpty() && entry.m_sEquipmentSlot != equipmentSlot)
			return false;
		if (!source.IsEmpty() && entry.m_aSourceAddons.Find(source) < 0)
			return false;
		if (faction == "Unassigned" && !entry.m_sFactions.IsEmpty())
			return false;
		if (!faction.IsEmpty() && faction != "Unassigned" && !entry.m_sFactions.Contains(" " + faction + " "))
			return false;
		if (mountType && (!entry.m_AttachmentType || !entry.m_AttachmentType.IsInherited(mountType)))
			return false;
		foreach (string term : terms)
		{
			if (!term.IsEmpty() && !entry.m_sSearch.Contains(term))
				return false;
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	//! Returns total matches in the current partial index; results contains one bounded page.
	//! Equipment slots and mount types are declared candidates, not insertion permission.
	int Search(GC_ItemCatalogueQuery query, int offset, int limit, out notnull array<GC_ItemCatalogueEntry> results)
	{
		results.Clear();
		if (!query || offset < 0 || limit < 1 || limit > 100)
			return -1;
		// The catalogue is append-only. Paging reuses the current query, and
		// background indexing evaluates only newly appended metadata.
		if (!m_CachedQuery || !m_CachedQuery.Equals(query))
		{
			m_CachedQuery = query.Copy();
			m_iSearchIndexed = 0;
			m_aSearchMatches.Clear();
			m_aSearchKeys.Clear();
			m_mSearchEntries.Clear();
			m_aSearchTerms.Clear();
			string text = query.m_sText;
			text.ToLower();
			text.Split(" ", m_aSearchTerms, true);
		}
		int previousMatches = m_aSearchMatches.Count();
		for (int index = m_iSearchIndexed; index < Count(); index++)
		{
			if (query.m_bPortableOnly && !Get(index).m_bPortable)
				continue;
			if (!Matches(index, m_aSearchTerms, query.m_sCategory, query.m_sEquipmentSlot, query.m_sSourceAddon, query.m_sFaction, query.m_MountType))
				continue;
			if (query.m_bWeaponItems && !query.MatchesWeapon(Get(index)))
				continue;
			if (query.m_bAlphabetical)
			{
				string key = Get(index).GetSortKey(query.m_bDescending);
				m_aSearchKeys.Insert(key);
				m_mSearchEntries.Insert(key, Get(index));
			}
			m_aSearchMatches.Insert(Get(index));
		}
		m_iSearchIndexed = Count();
		int total = m_aSearchMatches.Count();
		if (query.m_bAlphabetical)
		{
			if (total != previousMatches)
				m_aSearchKeys.Sort(query.m_bDescending);
			for (int position = offset; position < total && results.Count() < limit; position++)
				results.Insert(m_mSearchEntries.Get(m_aSearchKeys[position]));
		}
		else
		{
			for (int position = offset; position < total && results.Count() < limit; position++)
				results.Insert(m_aSearchMatches[position]);
		}
		return total;
	}
}

class GC_ItemCatalogueQuery
{
	bool m_bPortableOnly;
	bool m_bAlphabetical;
	bool m_bDescending;
	string m_sText;
	string m_sCategory;
	string m_sEquipmentSlot;
	//! Matches contributing source addons (including inheritance), not exclusive ownership.
	string m_sSourceAddon;
	//! Exact loaded faction key; "Unassigned" means no match in observed faction catalogues.
	string m_sFaction;
	typename m_MountType;
	bool m_bWeaponItems;
	ref array<typename> m_aMagazineWells = {};
	ref array<typename> m_aAttachmentTypes = {};

	//------------------------------------------------------------------------------------------------
	//! Snapshot mutable filters, including weapon compatibility arrays.
	GC_ItemCatalogueQuery Copy()
	{
		GC_ItemCatalogueQuery copy = new GC_ItemCatalogueQuery();
		copy.m_bPortableOnly = m_bPortableOnly;
		copy.m_bAlphabetical = m_bAlphabetical;
		copy.m_bDescending = m_bDescending;
		copy.m_sText = m_sText;
		copy.m_sCategory = m_sCategory;
		copy.m_sEquipmentSlot = m_sEquipmentSlot;
		copy.m_sSourceAddon = m_sSourceAddon;
		copy.m_sFaction = m_sFaction;
		copy.m_MountType = m_MountType;
		copy.m_bWeaponItems = m_bWeaponItems;
		copy.m_aMagazineWells.Copy(m_aMagazineWells);
		copy.m_aAttachmentTypes.Copy(m_aAttachmentTypes);
		return copy;
	}

	//------------------------------------------------------------------------------------------------
	bool Equals(GC_ItemCatalogueQuery other)
	{
		if (!other || m_bPortableOnly != other.m_bPortableOnly || m_bAlphabetical != other.m_bAlphabetical
			|| m_bDescending != other.m_bDescending || m_sText != other.m_sText || m_sCategory != other.m_sCategory
			|| m_sEquipmentSlot != other.m_sEquipmentSlot || m_sSourceAddon != other.m_sSourceAddon
			|| m_sFaction != other.m_sFaction || m_MountType != other.m_MountType || m_bWeaponItems != other.m_bWeaponItems
			|| m_aMagazineWells.Count() != other.m_aMagazineWells.Count() || m_aAttachmentTypes.Count() != other.m_aAttachmentTypes.Count())
			return false;
		foreach (int index, typename well : m_aMagazineWells)
		{
			if (well != other.m_aMagazineWells[index])
				return false;
		}
		foreach (int index, typename attachment : m_aAttachmentTypes)
		{
			if (attachment != other.m_aAttachmentTypes[index])
				return false;
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	//! Same inheritance direction as the native magazine and compatible-attachment predicates.
	bool MatchesWeapon(GC_ItemCatalogueEntry entry)
	{
		foreach (typename well : m_aMagazineWells)
		{
			if (entry.m_MagazineWell && entry.m_MagazineWell.IsInherited(well))
				return true;
		}
		foreach (typename mount : m_aAttachmentTypes)
		{
			if (entry.m_AttachmentType && entry.m_AttachmentType.IsInherited(mount))
				return true;
		}
		return false;
	}
}
