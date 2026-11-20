[ComponentEditorProps(category: "GC Lobby/Mission", description: "Samples living players after native spawn and deletion events for peak population conditions.")]
class GC_PeakFactionPopulationComponentClass : ScriptComponentClass
{
}

class GC_PeakFactionPopulationComponent : ScriptComponent
{
	protected GC_LobbyGameMode m_Mode;
	protected bool m_bRecountPending;
	protected bool m_bHasSample;
	protected ref map<string, int> m_mLiving = new map<string, int>();
	protected ref map<string, int> m_mPeak = new map<string, int>();

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (SCR_Global.IsEditMode() || !Replication.IsServer())
			return;
		m_Mode = GC_LobbyGameMode.Cast(owner);
		if (!m_Mode)
			return;
		m_Mode.GetOnPlayerSpawned().Insert(OnPlayerUpdated);
		m_Mode.GetOnPlayerDeleted().Insert(OnPlayerUpdated);
	}

	override void OnDelete(IEntity owner)
	{
		if (m_Mode)
		{
			m_Mode.GetOnPlayerSpawned().Remove(OnPlayerUpdated);
			m_Mode.GetOnPlayerDeleted().Remove(OnPlayerUpdated);
		}
		GetGame().GetCallqueue().Remove(Recount);
		super.OnDelete(owner);
	}

	protected bool IsActiveMatch()
	{
		return m_Mode && m_Mode == GC_LobbyGameMode.GetGameMode() && m_Mode.GetState() == SCR_EGameModeState.GAME;
	}

	protected void OnPlayerUpdated(int playerId, IEntity entity)
	{
		if (!IsActiveMatch() || m_bRecountPending)
			return;
		m_bRecountPending = true;
		GetGame().GetCallqueue().CallLater(Recount, 5000, false);
	}

	protected void Recount()
	{
		m_bRecountPending = false;
		if (!IsActiveMatch())
			return;
		SCR_FactionManager factions = SCR_FactionManager.Cast(GetGame().GetFactionManager());
		PlayerManager players = GetGame().GetPlayerManager();
		if (!factions || !players)
			return;

		m_mLiving.Clear();
		array<int> ids = {};
		players.GetPlayers(ids);
		foreach (int id : ids)
		{
			SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(players.GetPlayerControlledEntity(id));
			if (!character || !SCR_AIDamageHandling.IsAlive(character) || character.FindComponent(GC_LobbySpectatorBodyComponent))
				continue;
			Faction faction = factions.GetPlayerFaction(id);
			if (!faction)
				continue;
			string key = faction.GetFactionKey();
			if (!key.IsEmpty())
				m_mLiving.Set(key, m_mLiving.Get(key) + 1);
		}
		foreach (string key, int count : m_mLiving)
		{
			if (count > m_mPeak.Get(key))
				m_mPeak.Set(key, count);
		}
		m_bHasSample = true;
	}

	bool HasLostProportion(FactionKey faction, float ratio)
	{
		FactionManager factions = GetGame().GetFactionManager();
		return IsActiveMatch() && m_bHasSample && factions && factions.GetFactionByKey(faction)
			&& m_mLiving.Get(faction) <= m_mPeak.Get(faction) * (1 - ratio);
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Peak Faction Population Drop")]
class GC_PeakFactionPopulationCheck : GC_MissionScript
{
	[Attribute("", UIWidgets.Auto, "Examine living players of this faction. Requires the peak population component on the game mode.")]
	FactionKey m_sFaction;
	[Attribute("0.8", UIWidgets.Slider, "Required loss as a proportion of the historical peak.", "0 1 0.01")]
	float m_fRatio;

	override bool IsValid()
	{
		return !m_sFaction.IsEmpty() && m_fRatio >= 0 && m_fRatio <= 1;
	}

	override bool Run(GC_LobbyManager manager)
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode || manager != GC_LobbyManager.GetManager() || !Replication.IsServer())
			return false;
		GC_PeakFactionPopulationComponent observer = GC_PeakFactionPopulationComponent.Cast(mode.FindComponent(GC_PeakFactionPopulationComponent));
		return observer && observer.HasLostProportion(m_sFaction, m_fRatio);
	}
}
