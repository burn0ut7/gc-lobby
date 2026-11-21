//! Read resolved prefab metadata without creating a simulation entity.
class GC_VehicleCatalogueEntry : GC_ItemCatalogueEntry
{
	override string GetCategoryLabel()
	{
		if (m_sCategory.IsEmpty())
			return "Motorbike";
		return m_sCategory;
	}

	//------------------------------------------------------------------------------------------------
	protected static string GetBrowserCategory(EVehicleType vehicleType, string typeName, bool helicopter, ResourceName model)
	{
		if (helicopter)
			return "Helicopters";
		// Variants inherit the hull mesh even when their editable prefab names or vehicle types differ.
		string hull = model.GetPath();
		hull.ToLower();
		if (hull.Contains("/t14/"))
			return "Tanks";
		if (hull.Contains("/bmp") || hull.Contains("/stryker") || hull.Contains("/m2a2/") || hull.Contains("/lav25/") || hull.Contains("/k17bumerang/"))
			return "Infantry fighting vehicles";
		if (hull.Contains("/m113"))
			return "Armored personnel carriers";
		if (hull.Contains("/moto/") || hull.Contains("/motorbike") || hull.Contains("/motorcycle"))
			return string.Empty;
		switch (vehicleType)
		{
			case EVehicleType.CAR:
				return "Cars";
			case EVehicleType.TRUCK:
			case EVehicleType.FUEL_TRUCK:
			case EVehicleType.COMM_TRUCK:
			case EVehicleType.SUPPLY_TRUCK:
			case EVehicleType.REPAIR:
			case EVehicleType.ARSENAL:
				return "Trucks";
			case EVehicleType.APC:
				return "Armored personnel carriers";
			case EVehicleType.MORTAR:
				return "Mortars";
		}
		if (typeName.EndsWith("_TANK") || typeName == "TANK")
			return "Tanks";
		return "Other vehicles";
	}

	//------------------------------------------------------------------------------------------------
	static GC_VehicleCatalogueEntry Read(ResourceName prefab, Resource resource, IEntitySource entity)
	{
		if (!entity)
			return null;
		typename vehicleClass = entity.GetClassName().ToType();
		if (!vehicleClass || !vehicleClass.IsInherited(Vehicle))
			return null;

		ResourceName model;
		bool replicated;
		bool helicopter;
		for (int i, count = entity.GetComponentCount(); i < count; i++)
		{
			IEntityComponentSource component = entity.GetComponent(i);
			if (!component)
				continue;
			if (component.GetClassName() == "MeshObject")
				component.Get("Object", model);
			if (component.GetClassName() == "RplComponent")
				replicated = true;
			typename componentType = component.GetClassName().ToType();
			if (componentType && componentType.IsInherited(VehicleHelicopterSimulation))
				helicopter = true;
		}
		if (!replicated || model.IsEmpty() || model.GetPath().EndsWith("default.xob"))
			return null;

		GC_VehicleCatalogueEntry entry = new GC_VehicleCatalogueEntry();
		entry.m_sPrefab = prefab;
		entry.m_sName = FilePath.StripExtension(FilePath.StripPath(prefab));
		IEntityComponentSource editable = SCR_EditableEntityComponentClass.GetEditableEntitySource(resource);
		if (editable)
		{
			BaseContainer info = editable.GetObject("m_UIInfo");
			if (info)
				info.Get("Name", entry.m_sNameKey);
			string translated = WidgetManager.Translate(entry.m_sNameKey);
			if (!translated.IsEmpty() && !translated.StartsWith("#"))
				entry.m_sName = translated;
		}
		EVehicleType vehicleType;
		entity.Get("m_eVehicleType", vehicleType);
		string typeName = typename.EnumToString(EVehicleType, vehicleType);
		entry.m_sCategory = GetBrowserCategory(vehicleType, typeName, helicopter, model);
		typeName.Replace("_", " ");
		entity.GetSourceAddons(entry.m_aSourceAddons);
		entry.m_sSearch = entry.m_sName + " " + prefab + " " + entity.GetClassName() + " " + typeName + " " + entry.GetCategoryLabel();
		foreach (string addon : entry.m_aSourceAddons)
			entry.m_sSearch += " " + addon;
		entry.m_sSearch.ToLower();
		return entry;
	}
}
