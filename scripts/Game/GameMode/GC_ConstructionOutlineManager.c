//! Opt-in outline registry for missions that author a construction threshold.
[BaseContainerProps(configRoot: true)]
class GC_ConstructionOutlineManager : SCR_CampaignBuildingCompositionOutlineManager
{
	[Attribute("300", UIWidgets.EditBox, "Seconds from Match start before the normal construction threshold applies.")]
	protected float m_fPreparationSeconds;

	[Attribute("2", UIWidgets.EditBox, "Multiplier applied to construction thresholds through preparation.")]
	protected float m_fPreparationMultiplier;

	[Attribute("10", UIWidgets.EditBox, "Multiplier applied to new construction thresholds after preparation.")]
	protected float m_fMatchMultiplier;

	override int GetCompositionBuildingValue(ResourceName originalComposition)
	{
		int value = super.GetCompositionBuildingValue(originalComposition);
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode || !GC_LobbyPhase.IsValid(mode.GetState()) || value <= 0)
			return value;

		bool preparation = !GC_LobbyPhase.IsAtOrAfter(mode.GetState(), SCR_EGameModeState.GAME)
			|| mode.GetElapsedTime() < m_fPreparationSeconds;
		float multiplier = m_fMatchMultiplier;
		if (preparation)
			multiplier = m_fPreparationMultiplier;
		if (multiplier < 0.5 || multiplier * value >= 2147483648.0)
			return value;

		return multiplier * value;
	}
}
