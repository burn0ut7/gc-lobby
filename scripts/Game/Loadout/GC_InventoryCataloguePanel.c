//! Search presentation and bounded previews inside the native inventory menu.
class GC_InventoryCataloguePanel : ScriptedWidgetEventHandler
{
	protected Widget m_Root;
	protected Widget m_Vicinity;
	protected Widget m_Opened;
	protected EditBoxWidget m_Search;
	protected Widget m_ClearSearch;
	protected SCR_ComboBoxComponent m_Sort;
	protected SCR_ComboBoxComponent m_Mod;
	protected Widget m_DestinationRoot;
	protected SCR_ComboBoxComponent m_Destination;
	protected Widget m_InventoryContent;
	protected Widget m_VehiclePanel;
	protected ItemPreviewWidget m_VehiclePreview;
	protected Widget m_VehicleSpawn;
	protected Widget m_VehicleCategories;
	protected ref array<string> m_aVehicleClasses = {};
	protected ResourceName m_sSelectedVehicle;
	protected bool m_bVehicleView;
	protected Widget m_CratePanel;
	protected Widget m_CrateStorages;
	protected bool m_bCrateView;
	protected bool m_bInventoryContentVisible;
	protected Widget m_PlayerWeight;
	protected bool m_bPlayerWeightVisible;
	protected ref GC_GearQuantityDialog m_QuantityDialog;
	protected ref array<string> m_aModIDs = {""};
	protected ref map<string, string> m_mModTitles = new map<string, string>();
	protected ref GC_ItemCatalogue m_Catalogue;
	protected ref GC_ItemCatalogueQuery m_Query = new GC_ItemCatalogueQuery();
	protected ref array<GC_ItemCatalogueEntry> m_aPage = {};
	protected ref array<Widget> m_aCells = {};
	protected ref map<Widget, ResourceName> m_mPreviewPrefabs = new map<Widget, ResourceName>();
	protected int m_iOffset;
	protected int m_iTotal;
	protected int m_iPreview;
	protected int m_iIndexed = -1;
	protected float m_fRefresh;
	protected string m_sPendingText;
	protected float m_fSearchDelay;
	protected float m_fFeedbackTime;
	protected bool m_bFinished;
	protected SCR_SpinBoxPagingComponent m_Paging;
	protected int m_iPageCount = -1;
	protected float m_fVicinityOpacity;
	protected float m_fOpenedOpacity;
	protected bool m_bPagingActive;
	protected ref array<SCR_ButtonBaseComponent> m_aButtons = {};
	protected string m_sCategoryLabel = "All items";
	protected BaseInventoryStorageComponent m_WeaponStorage;
	protected IEntity m_WeaponEntity;
	protected InventoryStorageSlot m_InspectionSlot;
	protected bool m_bInspectionContext;
	protected int m_iWeaponSlot;
	protected string m_sWeaponSlotType;
	protected string m_sWeaponSignature;
	protected float m_fWeaponRefresh;
	protected static const int PAGE_SIZE = 10;

	//------------------------------------------------------------------------------------------------
	bool Attach(Widget inventoryRoot)
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode)
			return false;
		Widget left = inventoryRoot.FindAnyWidget("LeftArea");
		m_Vicinity = inventoryRoot.FindAnyWidget("StorageLootSlot");
		m_Opened = inventoryRoot.FindAnyWidget("OpenedStorages");
		if (!left || !m_Vicinity || !m_Opened)
			return false;
		m_Root = GetGame().GetWorkspace().CreateWidgets("{DC391DA320AE1001}UI/layouts/Inventory/GC_InventoryCatalogue.layout", inventoryRoot);
		if (!m_Root)
			return false;
		if (!m_Root.FindAnyWidget("Results") || !m_Root.FindAnyWidget("Status") || !m_Root.FindAnyWidget("CatalogueSearch"))
		{
			m_Root.RemoveFromHierarchy();
			m_Root = null;
			return false;
		}
		m_fVicinityOpacity = m_Vicinity.GetOpacity();
		m_fOpenedOpacity = m_Opened.GetOpacity();
		m_Vicinity.SetOpacity(0);
		m_Opened.SetOpacity(0);
		m_Root.AddHandler(this);
		array<string> buttonNames = {"CategoryAll", "CategoryWeapons", "CategoryMagazines", "CategoryGrenades", "CategoryHeadwear", "CategoryClothing", "CategoryVests", "CategoryBackpacks", "CategoryEquipment", "CategoryAttachments"};
		foreach (string buttonName : buttonNames)
			BindButton(m_Root.FindAnyWidget(buttonName));
		m_Paging = SCR_SpinBoxPagingComponent.GetSpinBoxPagingComponent("CataloguePaging", m_Root);
		m_Paging.m_OnChanged.Insert(OnPageChanged);
		m_Search = EditBoxWidget.Cast(m_Root.FindAnyWidget("CatalogueSearch"));
		m_ClearSearch = m_Root.FindAnyWidget("ClearSearch");
		BindButton(m_ClearSearch);
		m_Sort = SCR_ComboBoxComponent.Cast(m_Root.FindAnyWidget("CatalogueSort").FindHandler(SCR_ComboBoxComponent));
		m_Mod = SCR_ComboBoxComponent.Cast(m_Root.FindAnyWidget("CatalogueMod").FindHandler(SCR_ComboBoxComponent));
		m_Sort.AddItem("A–Z");
		m_Sort.AddItem("Z–A");
		m_Sort.SetCurrentItem(0, false, false, false);
		m_Mod.AddItem("All mods");
		m_Mod.SetCurrentItem(0, false, false, false);
		m_Sort.m_OnChanged.Insert(OnSearchOption);
		m_Mod.m_OnChanged.Insert(OnSearchOption);
		if (GC_GearSession.IsAdmin(GetGame().GetPlayerController().GetControlledEntity()))
		{
			m_DestinationRoot = GetGame().GetWorkspace().CreateWidgets("{618DBD5BF860D947}UI/layouts/Inventory/GC_AdminGearDestination.layout", inventoryRoot);
			m_Destination = SCR_ComboBoxComponent.Cast(m_DestinationRoot.FindAnyWidget("GearDestination").FindHandler(SCR_ComboBoxComponent));
			m_Destination.AddItem("#GC-Gear_Inventory");
			m_Destination.AddItem("#GC-Gear_Crate");
			m_Destination.AddItem("#GC-Vehicle_Browser");
			m_VehiclePanel = m_DestinationRoot.FindAnyWidget("VehiclePanel");
			m_VehiclePreview = ItemPreviewWidget.Cast(m_VehiclePanel.FindAnyWidget("VehiclePreview"));
			m_VehiclePreview.SetClearColor(false, 0);
			m_VehiclePreview.SetBlendMode(RenderTargetWidgetBlendMode.ALPHA_BLEND);
			m_VehicleSpawn = m_VehiclePanel.FindAnyWidget("SpawnVehicle");
			TextWidget.Cast(m_VehicleSpawn.FindAnyWidget("Text")).SetText("#GC-Vehicle_Spawn");
			BindButton(m_VehicleSpawn);
			m_VehicleCategories = m_Root.FindAnyWidget("VehicleCategories");
			AddVehicleCategory(string.Empty);
			m_Destination.SetCurrentItem(0, false, false, false);
			m_Destination.m_OnChanged.Insert(OnDestination);
			m_InventoryContent = inventoryRoot.FindAnyWidget("InventoryContent");
			m_bInventoryContentVisible = m_InventoryContent.IsVisible();
			m_PlayerWeight = inventoryRoot.FindAnyWidget("WeightOverlay");
			m_bPlayerWeightVisible = m_PlayerWeight.IsVisible();
			m_CratePanel = m_DestinationRoot.FindAnyWidget("GearCratePanel");
			m_CrateStorages = m_DestinationRoot.FindAnyWidget("GearCrateStorages");
			BindButton(m_DestinationRoot.FindAnyWidget("GearExit"));
		}
		for (int i; i < PAGE_SIZE; i++)
		{
			Widget cell = GetGame().GetWorkspace().CreateWidgets("{DC391DA320AE1002}UI/layouts/Inventory/GC_InventoryCatalogueCell.layout", m_Root.FindAnyWidget("Results"));
			m_aCells.Insert(cell);
			BindButton(cell.FindAnyWidget("SelectItem"));
			RenderTargetWidget preview = RenderTargetWidget.Cast(cell.FindAnyWidget("ItemPreview"));
			preview.SetClearColor(false, 0);
			preview.SetBlendMode(RenderTargetWidgetBlendMode.ALPHA_BLEND);
		}
		m_Catalogue = mode.GetGearCatalogue();
		m_Query.m_bPortableOnly = true;
		m_Query.m_bAlphabetical = true;
		Refresh();
		return true;
	}

	//------------------------------------------------------------------------------------------------
	void Detach()
	{
		if (m_QuantityDialog)
		{
			m_QuantityDialog.m_OnClose.Remove(OnQuantityClosed);
			m_QuantityDialog.Close();
			m_QuantityDialog = null;
		}
		if (m_bVehicleView)
		{
			m_InventoryContent.SetVisible(m_bInventoryContentVisible);
			m_PlayerWeight.SetVisible(m_bPlayerWeightVisible);
		}
		SetCrateView(false);
		if (m_Destination)
		{
			m_Destination.m_OnChanged.Remove(OnDestination);
			m_Destination.CloseList();
			m_Destination = null;
		}
		GC_CatalogueTooltip.HideCurrent();
		if (m_Sort)
		{
			m_Sort.m_OnChanged.Remove(OnSearchOption);
			m_Sort.CloseList();
		}
		if (m_Mod)
		{
			m_Mod.m_OnChanged.Remove(OnSearchOption);
			m_Mod.CloseList();
		}
		foreach (SCR_ButtonBaseComponent button : m_aButtons)
			button.m_OnClicked.Remove(OnButton);
		m_aButtons.Clear();
		if (m_DestinationRoot)
			m_DestinationRoot.RemoveFromHierarchy();
		m_DestinationRoot = null;
		if (m_Paging)
		{
			m_Paging.m_OnChanged.Remove(OnPageChanged);
			m_Paging.SetButtonsActive(false);
		}
		if (m_Root)
		{
			m_Root.RemoveHandler(this);
			m_Root.RemoveFromHierarchy();
			if (m_Vicinity)
				m_Vicinity.SetOpacity(m_fVicinityOpacity);
			if (m_Opened)
				m_Opened.SetOpacity(m_fOpenedOpacity);
		}
		m_Root = null;
		m_aCells.Clear();
		m_mPreviewPrefabs.Clear();
		m_aPage.Clear();
		m_Catalogue = null;
	}

	//------------------------------------------------------------------------------------------------
	void Tick(float timeSlice)
	{
		if (m_DestinationRoot)
		{
			GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
			bool ready = controller && !controller.GC_IsGearPending();
			m_Destination.GetRootWidget().SetEnabled(ready);
			if (m_bVehicleView)
			{
				m_VehicleSpawn.SetEnabled(ready && !m_sSelectedVehicle.IsEmpty());
				float width, height;
				m_VehiclePreview.GetParent().GetScreenSize(width, height);
				if (width > 0 && height > 0)
				{
					float inset = Math.Max(0, (1 - width / height) * 0.5);
					if (FrameSlot.GetAnchorMin(m_VehiclePreview)[1] != inset)
					{
						FrameSlot.SetAnchorMin(m_VehiclePreview, 0, inset);
						FrameSlot.SetAnchorMax(m_VehiclePreview, 1, 1 - inset);
					}
				}
			}
		}
		if (!m_Root)
			return;
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode)
			return;
		GC_ItemCatalogue catalogue = mode.GetGearCatalogue();
		if (catalogue != m_Catalogue)
		{
			// A successful authored save invalidates metadata as well as previews.
			m_Catalogue = catalogue;
			m_bFinished = false;
			m_iIndexed = -1;
			if (m_VehicleCategories)
			{
				Widget child = m_VehicleCategories.GetChildren();
				while (child)
				{
					Widget next = child.GetSibling();
					SCR_ButtonBaseComponent button = SCR_ButtonBaseComponent.Cast(child.GetChildren().FindHandler(SCR_ButtonBaseComponent));
					button.m_OnClicked.Remove(OnButton);
					m_aButtons.RemoveItem(button);
					child.RemoveFromHierarchy();
					child = next;
				}
				m_aVehicleClasses.Clear();
				AddVehicleCategory(string.Empty);
				if (m_bVehicleView)
					m_Query.m_sCategory = string.Empty;
			}
			m_iOffset = 0;
			m_mPreviewPrefabs.Clear();
			m_iPreview = 0;
			Refresh();
		}
		m_fWeaponRefresh += timeSlice;
		if (m_WeaponStorage && m_fWeaponRefresh >= 0.25)
		{
			m_fWeaponRefresh = 0;
			UpdateWeaponFilter();
		}
		if (m_fFeedbackTime > 0)
		{
			m_fFeedbackTime -= timeSlice;
			if (m_fFeedbackTime <= 0)
				Refresh();
		}
		bool pagingActive = GetGame().GetWorkspace().GetFocusedWidget() != m_Search && !m_Sort.IsOpened() && !m_Mod.IsOpened();
		if (pagingActive != m_bPagingActive)
		{
			m_bPagingActive = pagingActive;
			m_Paging.SetButtonsActive(pagingActive);
		}
		string text = m_Search.GetText();
		m_ClearSearch.SetVisible(!text.IsEmpty());
		if (text != m_sPendingText)
		{
			m_sPendingText = text;
			m_fSearchDelay = 0.15;
		}
		m_fSearchDelay -= timeSlice;
		if (m_fSearchDelay <= 0 && m_Query.m_sText != m_sPendingText)
		{
			m_Query.m_sText = m_sPendingText;
			m_iOffset = 0;
			Refresh();
		}
		m_fRefresh += timeSlice;
		float refreshInterval = 0.5;
		if (m_aPage.Count() < PAGE_SIZE)
			refreshInterval = 0.1;
		if (m_bFinished != m_Catalogue.IsFinished() || (m_fRefresh >= refreshInterval && m_iIndexed != ActiveCatalogue().Count()))
			Refresh(true);
		// Only visible cells, one request per frame. A new query cancels the old page queue.
		while (m_iPreview < m_aPage.Count() && m_mPreviewPrefabs.Get(m_aCells[m_iPreview]) == m_aPage[m_iPreview].m_sPrefab)
			m_iPreview++;
		if (m_iPreview < m_aPage.Count())
		{
			ChimeraWorld world = GetGame().GetWorld();
			ItemPreviewManagerEntity manager = world.GetItemPreviewManager();
			if (manager)
			{
				ItemPreviewWidget preview = ItemPreviewWidget.Cast(m_aCells[m_iPreview].FindAnyWidget("ItemPreview"));
				manager.SetPreviewItemFromPrefab(preview, m_aPage[m_iPreview].m_sPrefab, null, false, 1000);
				m_mPreviewPrefabs.Remove(m_aCells[m_iPreview]);
				m_mPreviewPrefabs.Insert(m_aCells[m_iPreview], m_aPage[m_iPreview].m_sPrefab);
				preview.SetVisible(true);
				m_iPreview++;
			}
		}
	}

	//------------------------------------------------------------------------------------------------
	void SetSlot(string slot)
	{
		if (m_bVehicleView)
			return;
		bool wasWeapon = m_WeaponStorage != null;
		m_WeaponStorage = null;
		m_WeaponEntity = null;
		m_InspectionSlot = null;
		m_bInspectionContext = false;
		m_Query.m_bWeaponItems = false;
		m_Query.m_aMagazineWells.Clear();
		m_Query.m_aAttachmentTypes.Clear();
		if (!wasWeapon && m_Query.m_sEquipmentSlot == slot && m_Query.m_sCategory.IsEmpty())
			return;
		m_Query.m_sCategory = string.Empty;
		m_sCategoryLabel = "All items";
		m_Query.m_sEquipmentSlot = slot;
		m_iOffset = 0;
		Refresh();
	}

	//------------------------------------------------------------------------------------------------
	void SetWeaponSlot(BaseInventoryStorageComponent storage, int slotID, string slotType)
	{
		if (m_bVehicleView)
			return;
		SetSlot(string.Empty);
		m_WeaponStorage = storage;
		m_iWeaponSlot = slotID;
		m_sWeaponSlotType = slotType;
		m_sWeaponSignature = string.Empty;
		UpdateWeaponFilter();
	}

	//------------------------------------------------------------------------------------------------
	//! Read only the selected live weapon; catalogue candidates remain metadata, never spawned previews.
	protected void UpdateWeaponFilter()
	{
		if (!m_WeaponStorage || m_iWeaponSlot < 0 || m_iWeaponSlot >= m_WeaponStorage.GetSlotsCount())
			return;
		IEntity item = m_WeaponStorage.GetSlot(m_iWeaponSlot).GetAttachedEntity();
		BaseWeaponComponent weapon;
		if (item)
			weapon = BaseWeaponComponent.Cast(item.FindComponent(BaseWeaponComponent));
		m_Query.m_aMagazineWells.Clear();
		m_Query.m_aAttachmentTypes.Clear();
		m_Query.m_bWeaponItems = weapon != null;
		m_WeaponEntity = item;
		string signature = "empty:" + m_sWeaponSlotType;
		if (weapon)
		{
			signature = item.GetPrefabData().GetPrefabName();
			array<BaseMuzzleComponent> muzzles = {};
			weapon.GetMuzzlesList(muzzles);
			foreach (BaseMuzzleComponent muzzle : muzzles)
			{
				BaseMagazineWell well = muzzle.GetMagazineWell();
				if (!well)
					continue;
				m_Query.m_aMagazineWells.Insert(well.Type());
				signature += "|mag:" + well.Type().ToString();
			}
			array<AttachmentSlotComponent> attachments = {};
			weapon.GetAttachments(attachments);
			foreach (AttachmentSlotComponent attachment : attachments)
			{
				BaseAttachmentType mount = attachment.GetAttachmentSlotType();
				if (!mount || !attachment.ShouldShowInInspection())
					continue;
				m_Query.m_aAttachmentTypes.Insert(mount.Type());
				signature += "|mount:" + mount.Type().ToString();
			}
		}
		if (signature == m_sWeaponSignature)
			return;
		m_sWeaponSignature = signature;
		m_Query.m_sEquipmentSlot = m_sWeaponSlotType;
		m_sCategoryLabel = "Weapons";
		if (weapon)
		{
			m_Query.m_sEquipmentSlot = string.Empty;
			m_sCategoryLabel = string.Empty;
		}
		m_iOffset = 0;
		Refresh();
	}

	//------------------------------------------------------------------------------------------------
	bool HasWeaponContext()
	{
		return m_Query.m_bWeaponItems;
	}

	//------------------------------------------------------------------------------------------------
	void SetInspectionSlot(InventoryStorageSlot slot, typename type)
	{
		if (m_bVehicleView)
			return;
		SetSlot(string.Empty);
		m_iOffset = 0;
		m_InspectionSlot = slot;
		m_bInspectionContext = true;
		m_Query.m_bWeaponItems = true;
		m_sCategoryLabel = string.Empty;
		if (type && type.IsInherited(BaseMagazineWell))
			m_Query.m_aMagazineWells.Insert(type);
		else if (type)
			m_Query.m_aAttachmentTypes.Insert(type);
		Refresh();
	}

	//------------------------------------------------------------------------------------------------
	bool HasInspectionContext()
	{
		return m_bInspectionContext;
	}

	//------------------------------------------------------------------------------------------------
	InventoryStorageSlot GetInspectionTarget()
	{
		return m_InspectionSlot;
	}

	//------------------------------------------------------------------------------------------------
	IEntity GetWeaponTarget()
	{
		if (!m_Query.m_bWeaponItems || !m_WeaponStorage || !m_WeaponEntity || m_iWeaponSlot < 0 || m_iWeaponSlot >= m_WeaponStorage.GetSlotsCount())
			return null;
		if (m_WeaponStorage.GetSlot(m_iWeaponSlot).GetAttachedEntity() != m_WeaponEntity)
			return null;
		return m_WeaponEntity;
	}

	//------------------------------------------------------------------------------------------------
	protected void OnSearchOption(SCR_ComboBoxComponent combo, int index)
	{
		if (combo == m_Sort)
			m_Query.m_bDescending = index == 1;
		else if (m_aModIDs.IsIndexValid(index))
			m_Query.m_sSourceAddon = m_aModIDs[index];
		m_iOffset = 0;
		Refresh();
	}

	//------------------------------------------------------------------------------------------------
	protected void PopulateMods()
	{
		m_Mod.m_OnChanged.Remove(OnSearchOption);
		m_Mod.ClearAll();
		m_Mod.AddItem("All mods");
		m_aModIDs.Clear();
		m_aModIDs.Insert("");
		m_mModTitles.Clear();
		array<string> contributing = {};
		GC_ItemCatalogue active = ActiveCatalogue();
		for (int i; i < active.Count(); i++)
		{
			GC_ItemCatalogueEntry entry = active.Get(i);
			if (!m_bVehicleView && !entry.m_bPortable)
				continue;
			foreach (string source : entry.m_aSourceAddons)
			{
				if (contributing.Find(source) < 0)
					contributing.Insert(source);
			}
		}
		array<string> addons = {};
		GameProject.GetLoadedAddons(addons);
		array<string> keys = {};
		map<string, string> labels = new map<string, string>();
		map<string, string> ids = new map<string, string>();
		foreach (string guid : addons)
		{
			string id = GameProject.GetAddonID(guid);
			if (contributing.Find(id) < 0)
				continue;
			string label = GameProject.GetAddonTitle(guid);
			if (label.IsEmpty())
				label = id;
			m_mModTitles.Insert(id, label);
			string key = label;
			key.ToLower();
			key += "\t" + id;
			keys.Insert(key);
			labels.Insert(key, label);
			ids.Insert(key, id);
		}
		keys.Sort();
		foreach (string key : keys)
		{
			m_Mod.AddItem(labels.Get(key));
			m_aModIDs.Insert(ids.Get(key));
		}
		int selected = Math.Max(0, m_aModIDs.Find(m_Query.m_sSourceAddon));
		m_Query.m_sSourceAddon = m_aModIDs[selected];
		m_Mod.SetCurrentItem(selected, false, false, false);
		m_Mod.m_OnChanged.Insert(OnSearchOption);
	}

	//------------------------------------------------------------------------------------------------
	protected void Refresh(bool background = false)
	{
		bool titlesChanged = !m_bFinished && m_Catalogue.IsFinished();
		if (titlesChanged)
			PopulateMods();
		array<GC_ItemCatalogueEntry> previousPage = {};
		previousPage.Copy(m_aPage);
		m_iTotal = ActiveCatalogue().Search(m_Query, m_iOffset, PAGE_SIZE, m_aPage);
		bool changed = previousPage.Count() != m_aPage.Count();
		if (!changed)
		{
			foreach (int position, GC_ItemCatalogueEntry entry : m_aPage)
			{
				if (entry.m_sPrefab != previousPage[position].m_sPrefab)
					changed = true;
			}
		}
		if (m_bVehicleView)
		{
			GC_ItemCatalogue vehicles = ActiveCatalogue();
			for (int v = Math.Max(0, m_iIndexed); v < vehicles.Count(); v++)
			{
				string category = vehicles.Get(v).m_sCategory;
				if (!m_aVehicleClasses.Contains(category))
					AddVehicleCategory(category);
			}
			if (m_aPage.IsEmpty())
				SelectVehicle(null);
			else
			{
				bool retained;
				foreach (GC_ItemCatalogueEntry candidate : m_aPage)
					if (candidate.m_sPrefab == m_sSelectedVehicle)
						retained = true;
				if (!retained)
					SelectVehicle(m_aPage[0]);
			}
		}
		m_iIndexed = ActiveCatalogue().Count();
		m_bFinished = m_Catalogue.IsFinished();
		m_fRefresh = 0;
		if (changed)
		{
			GC_CatalogueTooltip.HideCurrent();
			ReusePageCells(previousPage);
			m_iPreview = 0;
		}
		foreach (int index, Widget cell : m_aCells)
		{
			// Background matches on another page must not touch visible rows,
			// their previews, or the player's current hover/focus state.
			if (background && !changed && !titlesChanged)
				break;
			bool populated = index < m_aPage.Count();
			cell.SetVisible(populated);
			if (populated)
			{
				cell.FindAnyWidget("VehicleSelection").SetVisible(m_bVehicleView && m_aPage[index].m_sPrefab == m_sSelectedVehicle);
				// Match SCR_AddonTool.GetResourceLastAddon: the resolved resource's
				// last provider, rather than every inherited dependency.
				array<string> sources = m_aPage[index].m_aSourceAddons;
				string sourceText;
				if (!sources.IsEmpty())
				{
					string source = sources[sources.Count() - 1];
					sourceText = m_mModTitles.Get(source);
					if (sourceText.IsEmpty())
						sourceText = source;
				}
				TextWidget.Cast(cell.FindAnyWidget("SourceMods")).SetText(sourceText);
				TextWidget.Cast(cell.FindAnyWidget("ItemName")).SetText(m_aPage[index].m_sName);
				TextWidget.Cast(cell.FindAnyWidget("ItemCategory")).SetText(m_aPage[index].GetCategoryLabel());
			}
			if (populated)
				cell.FindAnyWidget("ItemPreview").SetVisible(m_mPreviewPrefabs.Get(cell) == m_aPage[index].m_sPrefab);
		}
		foreach (SCR_ButtonBaseComponent button : m_aButtons)
		{
			if (background)
				break;
			string name = button.GetRootWidget().GetName();
			if (m_bVehicleView && button.GetRootWidget().GetParent().GetParent() == m_VehicleCategories)
				button.SetToggled(m_aVehicleClasses[button.GetRootWidget().GetUserID()] == m_Query.m_sCategory, false, false);
			else if (name.StartsWith("Category"))
			{
				string selected = "Category" + m_sCategoryLabel;
				if (m_sCategoryLabel == "All items")
					selected = "CategoryAll";
				if (m_Query.m_sEquipmentSlot == "LoadoutHeadCoverArea")
					selected = "CategoryHeadwear";
				else if (m_Query.m_sEquipmentSlot == "LoadoutBackpackArea")
					selected = "CategoryBackpacks";
				else if (m_Query.m_sEquipmentSlot == "LoadoutVestArea" || m_Query.m_sEquipmentSlot == "LoadoutArmoredVestSlotArea")
					selected = "CategoryVests";
				else if (!m_Query.m_sEquipmentSlot.IsEmpty())
					selected = string.Empty;
				button.SetToggled(name == selected, false, false);
			}
		}
		int pages = Math.Max(1, Math.Ceil(m_iTotal / (float)PAGE_SIZE));
		string status = string.Format("%1 / %2", m_iOffset + m_aPage.Count(), m_iTotal);
		if (!m_bFinished)
			status += " | " + WidgetManager.Translate("#GC-Catalogue_Indexing");
		TextWidget statusWidget = TextWidget.Cast(m_Root.FindAnyWidget("Status"));
		if (m_fFeedbackTime <= 0 && statusWidget.GetText() != status)
			statusWidget.SetText(status);
		if (pages != m_iPageCount || m_Paging.GetCurrentIndex() != m_iOffset / PAGE_SIZE)
		{
			m_Paging.m_OnChanged.Remove(OnPageChanged);
			if (pages != m_iPageCount)
			{
				m_Paging.SetPageCount(pages, false);
				m_iPageCount = pages;
			}
			m_Paging.SetCurrentItem(m_iOffset / PAGE_SIZE, false);
			m_Paging.m_OnChanged.Insert(OnPageChanged);
		}
	}

	//------------------------------------------------------------------------------------------------
	protected void ReusePageCells(array<GC_ItemCatalogueEntry> previousPage)
	{
		array<Widget> available = {};
		available.Copy(m_aCells);
		array<Widget> ordered = {};
		// Keep each retained prefab's widget and render target, even if its row moves.
		foreach (GC_ItemCatalogueEntry entry : m_aPage)
		{
			Widget retained;
			foreach (int oldIndex, GC_ItemCatalogueEntry previous : previousPage)
			{
				if (previous.m_sPrefab == entry.m_sPrefab)
				{
					retained = m_aCells[oldIndex];
					available.RemoveItem(retained);
					break;
				}
			}
			ordered.Insert(retained);
		}
		for (int i; i < PAGE_SIZE; i++)
		{
			if (i >= ordered.Count())
				ordered.Insert(null);
			if (!ordered[i])
			{
				ordered[i] = available[0];
				available.Remove(0);
			}
			ordered[i].SetZOrder(i);
		}
		m_aCells.Copy(ordered);
	}

	//------------------------------------------------------------------------------------------------
	protected bool ProcessClick(Widget w)
	{
		if (w.GetName() == "GearExit")
		{
			SCR_InventoryMenuUI.GetInventoryMenu().Close();
			return true;
		}
		if (m_bVehicleView && w == m_VehicleSpawn)
		{
			GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
			if (controller && controller.GC_RequestVehicle(m_sSelectedVehicle) && controller.GC_IsGearPending())
				GearMessage("#GC-Vehicle_Pending");
			return true;
		}
		if (w == m_ClearSearch)
		{
			m_Search.SetText(string.Empty);
			m_sPendingText = string.Empty;
			m_Query.m_sText = string.Empty;
			m_fSearchDelay = 0;
			m_iOffset = 0;
			m_ClearSearch.SetVisible(false);
			GetGame().GetWorkspace().SetFocusedWidget(m_Search);
			Refresh();
			return true;
		}
		if (m_bVehicleView && w.GetParent().GetParent() == m_VehicleCategories)
		{
			m_Query.m_sCategory = m_aVehicleClasses[w.GetUserID()];
			m_iOffset = 0;
			Refresh();
			return true;
		}
		if (w.GetName().StartsWith("Category"))
		{
			string category = w.GetName();
			category.Replace("Category", "");
			SetSlot(string.Empty);
			m_Query.m_sCategory = string.Empty;
			m_sCategoryLabel = category;
			if (category == "All")
				m_sCategoryLabel = "All items";
			else
				m_Query.m_sCategory = category;
			m_iOffset = 0;
			Refresh();
			return true;
		}
		foreach (int index, Widget cell : m_aCells)
		{
			if (index < m_aPage.Count() && w == cell.FindAnyWidget("SelectItem"))
			{
				if (m_bVehicleView)
				{
					SelectVehicle(m_aPage[index]);
					Refresh();
					return true;
				}
				SCR_InventoryMenuUI.GetInventoryMenu().GC_ClickWeaponCatalogueItem(m_aPage[index]);
				return true;
			}
		}
		return false;
	}

	//------------------------------------------------------------------------------------------------
	GC_ItemCatalogueEntry EntryAt(Widget widget)
	{
		if (m_bVehicleView)
			return null;
		while (widget && widget != m_Root)
		{
			foreach (int index, Widget cell : m_aCells)
			{
				if (index < m_aPage.Count() && widget == cell.FindAnyWidget("SelectItem"))
					return m_aPage[index];
			}
			widget = widget.GetParent();
		}
		return null;
	}

	//------------------------------------------------------------------------------------------------
	void GearMessage(string message)
	{
		if (m_Destination)
		{
			m_Destination.GetRootWidget().SetEnabled(true);
			int index;
			if (GC_GearSession.s_bCrate)
				index = 1;
			if (m_bVehicleView)
				index = 2;
			m_Destination.SetCurrentItem(index, false, false, false);
		}
		m_fFeedbackTime = 4;
		TextWidget.Cast(m_Root.FindAnyWidget("Status")).SetText(message);
	}

	//------------------------------------------------------------------------------------------------
	protected void OnDestination(SCR_ComboBoxComponent combo, int index)
	{
		GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
		if (!controller || !GC_GearSession.IsAdmin(controller.GetControlledEntity()))
			return;
		if (controller.GC_IsGearPending())
		{
			int current;
			if (GC_GearSession.s_bCrate)
				current = 1;
			if (m_bVehicleView)
				current = 2;
			combo.SetCurrentItem(current, false, false, false);
			return;
		}
		SetVehicleView(index == 2);
		if (index != 1)
			GC_GearSession.s_bCrate = false;
		else
		{
			m_Destination.GetRootWidget().SetEnabled(false);
			controller.GC_RequestGearCrate(true);
		}
	}

	//------------------------------------------------------------------------------------------------
	protected GC_ItemCatalogue ActiveCatalogue()
	{
		if (m_bVehicleView)
			return m_Catalogue.GetVehicles();
		return m_Catalogue;
	}

	//------------------------------------------------------------------------------------------------
	protected void AddVehicleCategory(string category)
	{
		Widget bounds = GetGame().GetWorkspace().CreateWidgets("{842FC18B9C1365AD}UI/layouts/Inventory/GC_CatalogueCategory.layout", m_VehicleCategories);
		int index = m_aVehicleClasses.Insert(category);
		bounds.SetName("VehicleClass" + index);
		if (category == "Other vehicles")
			bounds.SetZOrder(int.MAX);
		else
			bounds.SetZOrder(index);
		Widget button = bounds.FindAnyWidget("CategoryAll");
		BindButton(button);
		button.SetName("VehicleFilter" + index);
		button.SetUserID(index);
		string label = category;
		if (label.IsEmpty())
			label = "#GC-Vehicle_All";
		TextWidget.Cast(button.FindAnyWidget("SourceMods")).SetText(label);
		ResourceName atlas = "{CB9C19A2219A4BFC}UI/Textures/Icons/Lobby_PS_Atlas_x64.imageset";
		string icon;
		switch (category)
		{
			case "Cars": icon = "Car"; break;
			case "Trucks": icon = "Truck"; break;
			case "Armored personnel carriers":
			case "Infantry fighting vehicles":
				icon = "APC";
				break;
			case "Tanks": icon = "Tank"; break;
			case "Helicopters": icon = "Helicopter"; break;
			case "Other vehicles": icon = "Dots"; break;
			default:
				atlas = "{3262679C50EF4F01}UI/Textures/Icons/icons_wrapperUI.imageset";
				icon = "gridView";
				if (category == "Mortars")
					icon = "artilleryFireMission";
				break;
		}
		ImageWidget image = ImageWidget.Cast(button.FindAnyWidget("Icon"));
		image.LoadImageFromSet(0, atlas, icon);
		int width, height;
		image.GetImageSize(0, width, height);
		if (width > 0 && height > 0)
		{
			float scale = 36.0 / Math.Max(width, height);
			float horizontalInset = (64 - width * scale) * 0.5;
			float verticalInset = (64 - height * scale) * 0.5;
			OverlaySlot.SetPadding(image, horizontalInset, verticalInset, horizontalInset, verticalInset);
		}
	}

	//------------------------------------------------------------------------------------------------
	protected void SelectVehicle(GC_ItemCatalogueEntry entry)
	{
		m_VehicleSpawn.SetEnabled(entry != null);
		TextWidget name = TextWidget.Cast(m_VehiclePanel.FindAnyWidget("VehicleName"));
		if (!entry)
		{
			m_sSelectedVehicle = string.Empty;
			m_VehiclePreview.SetVisible(false);
			name.SetText("#GC-Vehicle_Empty");
			return;
		}
		if (entry.m_sPrefab == m_sSelectedVehicle)
			return;
		m_sSelectedVehicle = entry.m_sPrefab;
		name.SetText(entry.m_sName);
		ChimeraWorld world = GetGame().GetWorld();
		ItemPreviewManagerEntity previews = world.GetItemPreviewManager();
		m_VehiclePreview.SetVisible(previews != null);
		if (previews)
			previews.SetPreviewItemFromPrefab(m_VehiclePreview, entry.m_sPrefab, null, false, 1000);
	}

	//------------------------------------------------------------------------------------------------
	protected void SetVehicleView(bool enabled)
	{
		if (enabled == m_bVehicleView)
			return;
		SCR_InventoryMenuUI.GetInventoryMenu().GC_PrepareCatalogueRefresh();
		SetCrateView(false);
		m_bVehicleView = enabled;
		m_WeaponStorage = null;
		m_WeaponEntity = null;
		m_InspectionSlot = null;
		m_bInspectionContext = false;
		m_Query = new GC_ItemCatalogueQuery();
		m_Query.m_bPortableOnly = !enabled;
		m_Query.m_bAlphabetical = true;
		m_Query.m_bDescending = m_Sort.GetCurrentIndex() == 1;
		m_sCategoryLabel = "All items";
		m_fFeedbackTime = 0;
		m_iOffset = 0;
		m_iIndexed = -1;
		m_Search.SetText(string.Empty);
		m_sPendingText = string.Empty;
		m_sSelectedVehicle = string.Empty;
		m_VehiclePanel.SetVisible(enabled);
		m_InventoryContent.SetVisible(!enabled && m_bInventoryContentVisible);
		m_PlayerWeight.SetVisible(!enabled && m_bPlayerWeightVisible);
		m_Root.FindAnyWidget("Categories").SetVisible(!enabled);
		m_Root.FindAnyWidget("VehicleCategoryRail").SetVisible(enabled);
		PopulateMods();
		Refresh();
	}

	//------------------------------------------------------------------------------------------------
	void SetCrateView(bool enabled)
	{
		if (!m_CratePanel || m_bCrateView == enabled)
			return;
		m_bCrateView = enabled;
		m_CratePanel.SetVisible(enabled);
		m_PlayerWeight.SetVisible(!enabled && m_bPlayerWeightVisible);
		if (enabled)
		{
			m_InventoryContent.SetVisible(false);
			m_Opened.SetName("GearHiddenOpenedStorages");
			m_CrateStorages.SetName("OpenedStorages");
		}
		else
		{
			m_CrateStorages.SetName("GearCrateStorages");
			m_Opened.SetName("OpenedStorages");
			m_InventoryContent.SetVisible(m_bInventoryContentVisible);
		}
	}

	//------------------------------------------------------------------------------------------------
	void HideCrateCapacityIndicator()
	{
		Widget progress = m_CrateStorages.FindAnyWidget("StorageSpaceIndicator");
		if (progress)
			progress.SetVisible(false);
	}

	//------------------------------------------------------------------------------------------------
	void TransferResult(bool success, bool deleting = false)
	{
		string label = "#GC-Catalogue_CannotPlace";
		if (success)
			label = "#GC-Catalogue_Added";
		if (deleting)
		{
			label = "#GC-Catalogue_CannotDelete";
			if (success)
				label = "#GC-Catalogue_Deleted";
		}
		m_fFeedbackTime = 2;
		TextWidget.Cast(m_Root.FindAnyWidget("Status")).SetText(label);
	}

	//------------------------------------------------------------------------------------------------
	protected void OnPageChanged(SCR_SpinBoxPagingComponent paging, int page)
	{
		PlayerController controller = GetGame().GetPlayerController();
		if (!controller || !GC_GearSession.IsActive(controller.GetControlledEntity()))
			return;
		m_iOffset = page * PAGE_SIZE;
		Refresh();
	}

	//------------------------------------------------------------------------------------------------
	override bool OnMouseWheel(Widget w, int x, int y, int wheel)
	{
		if (!m_Paging || m_Sort.IsOpened() || m_Mod.IsOpened())
			return false;
		if (wheel > 0)
			m_Paging.SetCurrentItem(m_Paging.GetCurrentIndex() - 1);
		else if (wheel < 0)
			m_Paging.SetCurrentItem(m_Paging.GetCurrentIndex() + 1);
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected void BindButton(Widget widget)
	{
		SCR_ButtonBaseComponent button = SCR_ButtonBaseComponent.Cast(widget.FindHandler(SCR_ButtonBaseComponent));
		if (widget.GetName().StartsWith("Category"))
			button.SetToggleable(true);
		button.m_OnClicked.Insert(OnButton);
		m_aButtons.Insert(button);
	}

	//------------------------------------------------------------------------------------------------
	protected void OnButton(SCR_ButtonBaseComponent button)
	{
		PlayerController controller = GetGame().GetPlayerController();
		if (!controller || !GC_GearSession.IsActive(controller.GetControlledEntity()))
			return;
		ProcessClick(button.GetRootWidget());
	}

	//------------------------------------------------------------------------------------------------
	override bool OnDoubleClick(Widget w, int x, int y, int button)
	{
		GC_ItemCatalogueEntry entry = EntryAt(w);
		GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
		if (button != 0 || !entry || !controller || !GC_GearSession.IsAdmin(controller.GetControlledEntity()))
			return false;
		if (m_QuantityDialog || controller.GC_IsGearPending() || SCR_ConfigurableDialogUi.GetCurrentDialog())
			return true;
		SCR_InventoryMenuUI.GetInventoryMenu().GC_PrepareCatalogueRefresh();
		GC_CatalogueTooltip.HideCurrent();
		m_QuantityDialog = new GC_GearQuantityDialog();
		m_QuantityDialog.Configure(entry);
		SCR_ConfigurableDialogUi.CreateFromPreset("{618DBD5BF860D948}Configs/UI/GC_GearQuantityDialog.conf", "quantity", m_QuantityDialog);
		m_QuantityDialog.m_OnClose.Insert(OnQuantityClosed);
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected void OnQuantityClosed()
	{
		if (m_QuantityDialog)
			m_QuantityDialog.m_OnClose.Remove(OnQuantityClosed);
		m_QuantityDialog = null;
	}

	//------------------------------------------------------------------------------------------------
	string State()
	{
		return string.Format("category=" + m_Query.m_sCategory + " faction=" + m_Query.m_sFaction + " descending=" + m_Query.m_bDescending + " slot=%1 text=%2 offset=%3 total=%4 visible=%5 previews=%6 indexed=%7 finished=%8", m_Query.m_sEquipmentSlot, m_Query.m_sText, m_iOffset, m_iTotal, m_aPage.Count(), m_iPreview, m_iIndexed, m_bFinished);
	}
}
