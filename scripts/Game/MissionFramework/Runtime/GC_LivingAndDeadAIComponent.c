[ComponentEditorProps(category: "GC Lobby/Mission", description: "Tracks living AI and confirmed AI deaths for a selected faction.")]
class GC_LivingAndDeadAIComponentClass : SCR_BaseGameModeComponentClass
{
}

class GC_LivingAndDeadAIComponent : SCR_BaseGameModeComponent
{
	[Attribute("", UIWidgets.Auto, "Faction whose living AI and confirmed deaths contribute to the total.")]
	protected FactionKey m_sFaction;

	protected ref GC_AreaPopulation m_Population = new GC_AreaPopulation();
	protected ref set<IEntity> m_aConfirmedDead = new set<IEntity>();
	protected int m_iLiving;
	protected int m_iDeaths;
	protected bool m_bSampleAvailable;

	protected bool OwnsAuthority()
	{
		return Replication.IsServer() && GC_LobbyGameMode.GetGameMode() == GetOwner() && !m_sFaction.IsEmpty();
	}

	override void OnGameModeStart()
	{
		super.OnGameModeStart();
		if (OwnsAuthority())
			GetGame().GetCallqueue().CallLater(Sample, 1000, true);
	}

	override void OnGameModeEnd(SCR_GameModeEndData data)
	{
		StopSampling();
		super.OnGameModeEnd(data);
	}

	override void OnDelete(IEntity owner)
	{
		StopSampling();
		super.OnDelete(owner);
	}

	protected void StopSampling()
	{
		GetGame().GetCallqueue().Remove(Sample);
		m_bSampleAvailable = false;
	}

	protected void Sample()
	{
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!OwnsAuthority() || !manager || manager.GetLobbyPhase() != SCR_EGameModeState.GAME)
		{
			StopSampling();
			return;
		}

		m_Population.Sample(manager, true);
		m_bSampleAvailable = m_Population.IsAvailable();
		m_iLiving = 0;
		foreach (GC_AreaCharacter entry : m_Population.m_aCharacters)
		{
			if (!entry.m_bPlayer && entry.m_sFaction == m_sFaction)
				m_iLiving++;
		}
	}

	override void OnControllableDestroyed(notnull SCR_InstigatorContextData instigatorContextData)
	{
		super.OnControllableDestroyed(instigatorContextData);
		if (!OwnsAuthority() || instigatorContextData.GetVictimPlayerID() > 0)
			return;
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || manager.GetLobbyPhase() == SCR_EGameModeState.POSTGAME)
			return;
		SCR_ChimeraCharacter victim = SCR_ChimeraCharacter.Cast(instigatorContextData.GetVictimEntity());
		if (!victim || victim.GetFactionKey() != m_sFaction || m_aConfirmedDead.Contains(victim)
			|| manager.GetSlotByEntity(victim) || victim.FindComponent(GC_LobbySpectatorBodyComponent))
			return;
		AIControlComponent control = AIControlComponent.Cast(victim.FindComponent(AIControlComponent));
		if (!control || !control.GetAIAgent())
			return;
		SCR_AIGroup group = SCR_AIGroup.Cast(control.GetAIAgent().GetParentGroup());
		if (group && group.IsPlayable())
			return;
		m_aConfirmedDead.Insert(victim);
		m_iDeaths++;
		// The next live sample excludes the corpse before evaluating the new total.
		m_bSampleAvailable = false;
	}

	bool HasLostProportion(float ratio)
	{
		int lives = m_iLiving + m_iDeaths;
		float deaths = m_iDeaths;
		return OwnsAuthority() && m_bSampleAvailable && lives > 0 && deaths / lives >= ratio;
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Living and Dead AI Casualties")]
class GC_LivingAndDeadAICheck : GC_MissionScript
{
	[Attribute("0.9", UIWidgets.Slider, "Required proportion of confirmed deaths among living and dead AI.", "0 1 0.01")]
	float m_fRatio;

	override bool IsValid()
	{
		return m_fRatio >= 0 && m_fRatio <= 1;
	}

	override bool Run(GC_LobbyManager manager)
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode || manager != GC_LobbyManager.GetManager() || !Replication.IsServer())
			return false;
		GC_LivingAndDeadAIComponent observer = GC_LivingAndDeadAIComponent.Cast(mode.FindComponent(GC_LivingAndDeadAIComponent));
		return observer && observer.HasLostProportion(m_fRatio);
	}
}
