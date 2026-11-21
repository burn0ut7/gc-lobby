[ComponentEditorProps(category: "GC Lobby/Mission", description: "Apply authored geographic, time zone and atmospheric settings for this mission.")]
class GC_MissionEnvironmentComponentClass : SCR_BaseGameModeComponentClass
{
}

class GC_MissionEnvironmentComponent : SCR_BaseGameModeComponent
{
	[Attribute("1", UIWidgets.CheckBox, "Override fog, precipitation and wind")]
	protected bool m_bOverrideAtmosphere;

	[Attribute("0", UIWidgets.Slider, "Fog amount", "0 1 0.01")]
	protected float m_fFogAmount;
	[Attribute("0", UIWidgets.Slider, "Fog height density", "0 150 0.1")]
	protected float m_fFogHeightDensity;
	[Attribute("0", UIWidgets.Slider, "Rain intensity", "0 1 0.01")]
	protected float m_fRainIntensity;
	[Attribute("0", UIWidgets.Slider, "Wind speed in metres per second", "0 50 0.1")]
	protected float m_fWindSpeed;

	[Attribute("0", UIWidgets.CheckBox, "Override geographic coordinates")]
	protected bool m_bOverrideGeographicCoordinates;

	[Attribute("49.019001", UIWidgets.EditBox, "Latitude in degrees")]
	protected float m_fLatitude;

	[Attribute("37.921001", UIWidgets.EditBox, "Longitude in degrees")]
	protected float m_fLongitude;

	[Attribute("0", UIWidgets.CheckBox, "Override local time zone")]
	protected bool m_bOverrideTimeZone;

	[Attribute("2", UIWidgets.EditBox, "UTC offset in hours")]
	protected float m_fTimeZoneOffset;

	[Attribute("0", UIWidgets.CheckBox, "Use daylight saving time")]
	protected bool m_bDSTEnabled;

	[Attribute("1", UIWidgets.EditBox, "Daylight saving offset in hours")]
	protected float m_fDSTOffsetHours;

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
		if (m_bOverrideGeographicCoordinates)
		{
			weather.SetCurrentLatitude(m_fLatitude);
			weather.SetCurrentLongitude(m_fLongitude);
		}
		if (m_bOverrideTimeZone)
		{
			weather.SetTimeZoneOffset(m_fTimeZoneOffset);
			weather.SetDSTOffset(m_fDSTOffsetHours);
			weather.SetDSTEnabled(m_bDSTEnabled);
		}
		if (!m_bOverrideAtmosphere)
			return;
		weather.SetFogAmountOverride(true, m_fFogAmount);
		weather.SetFogHeightDensityOverride(true, m_fFogHeightDensity);
		weather.SetRainIntensityOverride(true, m_fRainIntensity);
		weather.SetWindSpeedOverride(true, m_fWindSpeed);
	}
}
