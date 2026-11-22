class GC_MissionSupplyDepotComponentClass : SCR_BaseGameModeComponentClass
{
}

//! Holds the depot container selected by the authored initialization event.
class GC_MissionSupplyDepotComponent : SCR_BaseGameModeComponent
{
	protected SCR_ResourceContainer m_Container;

	//------------------------------------------------------------------------------------------------
	static SCR_ResourceContainer FindContainer(BaseWorld world, string name)
	{
		IEntity entity = world.FindEntityByName(name);
		if (!entity)
			return null;
		SCR_ResourceComponent resources = SCR_ResourceComponent.FindResourceComponent(entity, true);
		SCR_ResourceContainer container;
		if (resources && resources.GetContainer(EResourceType.SUPPLIES, container))
			return container;
		return null;
	}

	//------------------------------------------------------------------------------------------------
	bool Remember(string depot)
	{
		m_Container = FindContainer(GetOwner().GetWorld(), depot);
		return m_Container != null;
	}

	//------------------------------------------------------------------------------------------------
	bool Read(out float supplies)
	{
		if (!m_Container)
			return false;
		supplies = m_Container.GetResourceValue();
		return true;
	}
}

enum GC_EMissionSupplyOperation
{
	REMEMBER_DEPOT,
	FILL_SELECTED_CARGO,
	CHECK_DEPOT,
	CHECK_DEPOT_PHASES
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Named vehicle and depot supplies")]
class GC_MissionSupplyScript : GC_MissionScript
{
	[Attribute("0", UIWidgets.ComboBox, enums: {
		ParamEnum("Remember depot", "0"), ParamEnum("Fill selected cargo", "1"), ParamEnum("Check depot", "2"),
		ParamEnum("Check depot in two phases", "3")})]
	GC_EMissionSupplyOperation m_eOperation;
	[Attribute("", UIWidgets.EditBox)]
	string m_sDepot;
	[Attribute("", UIWidgets.Auto)]
	ref array<string> m_aChoiceFlags;
	[Attribute("", UIWidgets.Auto)]
	ref array<string> m_aCargoNames;
	[Attribute("0", UIWidgets.EditBox)]
	int m_iSupplies;
	[Attribute("", UIWidgets.EditBox)]
	string m_sResultFlag;
	[Attribute("0", UIWidgets.EditBox)]
	int m_iNextSupplies;
	[Attribute("", UIWidgets.EditBox)]
	string m_sNextFlag;

	//------------------------------------------------------------------------------------------------
	override bool IsValid()
	{
		switch (m_eOperation)
		{
			case GC_EMissionSupplyOperation.REMEMBER_DEPOT:
				return !m_sDepot.IsEmpty();
			case GC_EMissionSupplyOperation.FILL_SELECTED_CARGO:
				return m_iSupplies >= 0 && !m_sResultFlag.IsEmpty() && m_aChoiceFlags && !m_aChoiceFlags.IsEmpty()
					&& m_aCargoNames && m_aCargoNames.Count() == m_aChoiceFlags.Count()
					&& GC_RuleAction.ValidTargetNames(m_aChoiceFlags) && GC_RuleAction.ValidTargetNames(m_aCargoNames);
			case GC_EMissionSupplyOperation.CHECK_DEPOT:
				return m_iSupplies >= 0 && !m_sResultFlag.IsEmpty();
			case GC_EMissionSupplyOperation.CHECK_DEPOT_PHASES:
				return m_iSupplies >= 0 && m_iNextSupplies > m_iSupplies && !m_sResultFlag.IsEmpty()
					&& !m_sNextFlag.IsEmpty() && m_sResultFlag != m_sNextFlag;
		}
		return false;
	}

	//------------------------------------------------------------------------------------------------
	override bool Run(GC_LobbyManager manager)
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode)
			return false;
		if (m_eOperation == GC_EMissionSupplyOperation.FILL_SELECTED_CARGO)
			return FillSelectedCargo(manager, mode.GetWorld());

		GC_MissionSupplyDepotComponent depot = GC_MissionSupplyDepotComponent.Cast(
			mode.FindComponent(GC_MissionSupplyDepotComponent));
		if (!depot)
			return false;
		if (m_eOperation == GC_EMissionSupplyOperation.REMEMBER_DEPOT)
			return depot.Remember(m_sDepot);
		float supplies;
		if (!depot.Read(supplies))
			return false;
		if (m_eOperation == GC_EMissionSupplyOperation.CHECK_DEPOT_PHASES)
		{
			if (!manager.GetFlag(m_sResultFlag))
				return supplies < m_iSupplies || manager.CallFlag(m_sResultFlag);
			return supplies < m_iNextSupplies || manager.GetFlag(m_sNextFlag) || manager.CallFlag(m_sNextFlag);
		}
		if (supplies == m_iSupplies && !manager.GetFlag(m_sResultFlag))
			return manager.CallFlag(m_sResultFlag);
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected bool FillSelectedCargo(GC_LobbyManager manager, BaseWorld world)
	{
		string cargo;
		foreach (int index, string flag : m_aChoiceFlags)
		{
			if (manager.GetFlag(flag))
				cargo = m_aCargoNames[index];
		}
		SCR_ResourceContainer container;
		if (!cargo.IsEmpty())
			container = GC_MissionSupplyDepotComponent.FindContainer(world, cargo);
		if (!container)
			return !manager.GetFlag(m_sResultFlag) && manager.CallFlag(m_sResultFlag);
		container.SetResourceValue(m_iSupplies);
		return true;
	}
}
