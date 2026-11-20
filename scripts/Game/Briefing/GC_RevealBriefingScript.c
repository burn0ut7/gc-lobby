[BaseContainerProps(), BaseContainerCustomStringTitleField("Reveal Briefing")]
class GC_RevealBriefingScript : GC_MissionScript
{
	[Attribute("0", UIWidgets.SpinBox, "Index of the existing hidden briefing.", params: "0 32 1")]
	int m_iBriefingOrder;

	[Attribute("", UIWidgets.Auto, "Factions that can read the briefing after this action.")]
	ref array<FactionKey> m_aFactions;

	[Attribute("0", UIWidgets.CheckBox, "Also reveal the briefing to viewers without a faction.")]
	bool m_bVisibleWithoutFaction;

	override bool IsValid()
	{
		return m_iBriefingOrder >= 0 && m_iBriefingOrder <= GC_LobbyBriefingCatalog.MAX_BRIEFINGS
			&& (m_bVisibleWithoutFaction || (m_aFactions && !m_aFactions.IsEmpty()))
			&& (!m_aFactions || m_aFactions.Count() <= GC_Briefing.MAX_FACTION_KEYS);
	}

	override bool Run(GC_LobbyManager manager)
	{
		GC_Briefing briefing = manager.GetBriefing(m_iBriefingOrder);
		if (!briefing || briefing.GetAudience() != GC_EBriefingAudience.HIDDEN)
			return false;

		GC_EBriefingAudience audience = GC_EBriefingAudience.DEFAULT;
		if (m_bVisibleWithoutFaction)
			audience = GC_EBriefingAudience.LISTED_AND_UNASSIGNED;
		return manager.SetBriefing(briefing.GetOrder(), briefing.GetTitle(), briefing.GetContent(),
			GC_FactionKeys.ToStrings(m_aFactions), briefing.GetShowAtGameModeState(),
			briefing.IsGameModeStateInverted(), briefing.IsVisible(), audience);
	}
}
