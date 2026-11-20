[ComponentEditorProps(category: "GC Lobby/Mission", description: "Apply authored fog, precipitation and wind overrides for this mission.")]
class GC_MissionEnvironmentComponentClass : SCR_BaseGameModeComponentClass
{
}

class GC_MissionEnvironmentComponent : SCR_BaseGameModeComponent
{
	[Attribute("0", UIWidgets.Slider, "Fog amount", "0 1 0.01")]
	protected float m_fFogAmount;
	[Attribute("0", UIWidgets.Slider, "Fog height density", "0 150 0.1")]
	protected float m_fFogHeightDensity;
	[Attribute("0", UIWidgets.Slider, "Rain intensity", "0 1 0.01")]
	protected float m_fRainIntensity;
	[Attribute("0", UIWidgets.Slider, "Wind speed in metres per second", "0 50 0.1")]
	protected float m_fWindSpeed;

	override void OnWorldPostProcess(World world)
	{
		super.OnWorldPostProcess(world);
		if (!Replication.IsServer() || SCR_Global.IsEditMode() || GC_LobbyGameMode.GetGameMode() != GetOwner())
			return;
		ChimeraWorld missionWorld = ChimeraWorld.CastFrom(world);
		if (!missionWorld)
			return;
		TimeAndWeatherManagerEntity weather = missionWorld.GetTimeAndWeatherManager();
		if (!weather)
			return;
		weather.SetFogAmountOverride(true, m_fFogAmount);
		weather.SetFogHeightDensityOverride(true, m_fFogHeightDensity);
		weather.SetRainIntensityOverride(true, m_fRainIntensity);
		weather.SetWindSpeedOverride(true, m_fWindSpeed);
	}
}
