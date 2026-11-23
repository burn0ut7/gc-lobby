[ComponentEditorProps(category: "GC Lobby/Mission", description: "Samples living players at match start and after native player lifecycle events.")]
class GC_PeakFactionPopulationComponentClass : SCR_BaseGameModeComponentClass
{
}

class GC_PeakFactionPopulationComponent : SCR_BaseGameModeComponent
{
	[Attribute("", UIWidgets.Object, "Optional mission flags written in order after each Match population sample.")]
	protected ref array<ref GC_PeakFactionPopulationFlag> m_aObservedFlags;

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
		if (IsActiveMatch())
			ScheduleRecount();
	}

	override void OnGameModeStart()
	{
		super.OnGameModeStart();
		// Lobby may transfer a prepared character to the player without a spawn event.
		if (m_Mode && m_Mode == GC_LobbyGameMode.GetGameMode() && Replication.IsServer())
			ScheduleRecount();
	}

	override void OnControllableDestroyed(notnull SCR_InstigatorContextData instigatorContextData)
	{
		super.OnControllableDestroyed(instigatorContextData);
		if (IsActiveMatch() && instigatorContextData.GetVictimPlayerID() > 0)
			ScheduleRecount();
	}

	protected void ScheduleRecount()
	{
		if (m_bRecountPending)
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
		if (!m_aObservedFlags || m_aObservedFlags.IsEmpty())
			return;
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager)
			return;
		foreach (GC_PeakFactionPopulationFlag flag : m_aObservedFlags)
		{
			if (!flag || flag.m_sName.IsEmpty() || flag.m_fRatio < 0 || flag.m_fRatio > 1 || !factions.GetFactionByKey(flag.m_sFaction))
				continue;
			manager.GC_PublishPopulationFlag(this, flag.m_sName, ReachedLossThreshold(flag.m_sFaction, flag.m_fRatio));
		}
	}

	protected bool ReachedLossThreshold(FactionKey faction, float ratio)
	{
		return m_mLiving.Get(faction) <= m_mPeak.Get(faction) * (1 - ratio);
	}

	bool HasLostProportion(FactionKey faction, float ratio, bool inverted = false)
	{
		FactionManager factions = GetGame().GetFactionManager();
		if (!IsActiveMatch() || !m_bHasSample || !factions || !factions.GetFactionByKey(faction))
			return false;

		return ReachedLossThreshold(faction, ratio) != inverted;
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("m_sName")]
class GC_PeakFactionPopulationFlag
{
	[Attribute("", UIWidgets.EditBox, "Name of the Boolean mission flag to assign.")]
	string m_sName;
	[Attribute("", UIWidgets.Auto, "Faction whose living players are counted.")]
	FactionKey m_sFaction;
	[Attribute("1", UIWidgets.Slider, "Loss as a proportion of the historical peak.", "0 1 0.01")]
	float m_fRatio;
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Peak Faction Population Drop")]
class GC_PeakFactionPopulationCheck : GC_MissionScript
{
	[Attribute("", UIWidgets.Auto, "Examine living players of this faction. Requires the peak population component on the game mode.")]
	FactionKey m_sFaction;
	[Attribute("0.8", UIWidgets.Slider, "Required loss as a proportion of the historical peak.", "0 1 0.01")]
	float m_fRatio;
	[Attribute("0", UIWidgets.CheckBox, "Pass while losses are below the threshold after the first sample.")]
	bool m_bInvert;

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
		return observer && observer.HasLostProportion(m_sFaction, m_fRatio, m_bInvert);
	}
}
