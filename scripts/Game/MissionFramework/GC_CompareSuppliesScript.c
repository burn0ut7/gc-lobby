[BaseContainerProps(), BaseContainerCustomStringTitleField("Compare Depot Supplies")]
class GC_CompareSuppliesScript : GC_MissionScript
{
	[Attribute("", UIWidgets.EditBox, "Name of the first supply depot.")]
	string m_sFirstDepot;
	[Attribute("", UIWidgets.EditBox, "Name of the second supply depot.")]
	string m_sSecondDepot;
	[Attribute("", UIWidgets.EditBox, "Flag called when the first depot has more supplies.")]
	string m_sFirstFlag;
	[Attribute("", UIWidgets.EditBox, "Flag called when the second depot wins or ties.")]
	string m_sSecondFlag;
	[Attribute("", UIWidgets.EditBox, "Flag called if either depot's supply consumer is unavailable.")]
	string m_sErrorFlag;

	//------------------------------------------------------------------------------------------------
	override bool IsValid()
	{
		return !m_sFirstDepot.IsEmpty() && !m_sSecondDepot.IsEmpty() && m_sFirstDepot != m_sSecondDepot
			&& !m_sFirstFlag.IsEmpty() && !m_sSecondFlag.IsEmpty() && !m_sErrorFlag.IsEmpty()
			&& m_sFirstFlag != m_sSecondFlag && m_sFirstFlag != m_sErrorFlag && m_sSecondFlag != m_sErrorFlag;
	}

	//------------------------------------------------------------------------------------------------
	override bool Run(GC_LobbyManager manager)
	{
		SCR_ResourceConsumer first = FindDepot(GetGame().GetWorld(), m_sFirstDepot);
		SCR_ResourceConsumer second = FindDepot(GetGame().GetWorld(), m_sSecondDepot);
		if (!first || !second)
			return manager.CallFlag(m_sErrorFlag);
		if (first.GetAggregatedResourceValue() > second.GetAggregatedResourceValue())
			return manager.CallFlag(m_sFirstFlag);
		return manager.CallFlag(m_sSecondFlag);
	}

	//------------------------------------------------------------------------------------------------
	protected SCR_ResourceConsumer FindDepot(BaseWorld world, string name)
	{
		IEntity depot = world.FindEntityByName(name);
		if (!depot)
			return null;
		SCR_ResourceComponent resources = SCR_ResourceComponent.FindResourceComponent(depot, true);
		if (!resources)
			return null;
		return resources.GetConsumer(EResourceGeneratorID.DEFAULT_STORAGE, EResourceType.SUPPLIES);
	}
}
