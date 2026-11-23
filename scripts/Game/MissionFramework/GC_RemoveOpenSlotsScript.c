[BaseContainerProps(), BaseContainerCustomStringTitleField("Remove open mission slots")]
class GC_RemoveOpenSlotsScript : GC_MissionScript
{
	[Attribute("", UIWidgets.Auto, "Faction keys to remove. Leave empty to include all factions.")]
	ref array<string> m_aFactions;

	//------------------------------------------------------------------------------------------------
	override bool IsValid()
	{
		return GC_RuleAction.ValidTargetNames(m_aFactions);
	}

	//------------------------------------------------------------------------------------------------
	override bool Run(GC_LobbyManager manager)
	{
		if (!manager || !manager.AreMissionInteractionsActive())
			return false;

		array<string> factions = {};
		if (m_aFactions)
			factions = m_aFactions;
		manager.GC_RemoveOpenMissionSlots(factions);
		return true;
	}
}
