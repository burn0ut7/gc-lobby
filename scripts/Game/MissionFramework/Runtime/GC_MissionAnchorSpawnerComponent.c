[ComponentEditorProps(category: "GC Lobby/Mission", description: "Schedule spawns from this entity when its Match conditions first pass.")]
class GC_MissionAnchorSpawnerComponentClass : ScriptComponentClass
{
}

class GC_MissionAnchorSpawnerComponent : ScriptComponent
{
	[Attribute("", UIWidgets.Auto, "Flag required to schedule the spawn. Leave empty to schedule on Match entry.")]
	protected GC_MissionFlagName m_sRequiredFlag;

	[Attribute("", UIWidgets.Auto, "Delay in seconds for each spawn. Leave empty to spawn once on the next update.")]
	protected ref array<float> m_aDelaySeconds;

	[Attribute("", UIWidgets.Object, "Prefab and spawn configuration. Use this component's entity as the placement.")]
	protected ref GC_PrefabSpawn m_Spawn;

	protected GC_LobbyManager m_ScheduledManager;

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && GetGame().InPlayMode() && IsValid())
			GetGame().GetCallqueue().CallLater(BeginObserving, 1000, false);
	}

	protected bool IsValid()
	{
		if (!m_Spawn || !m_Spawn.IsValidAtAnchor())
			return false;
		if (!m_aDelaySeconds || m_aDelaySeconds.IsEmpty())
			return true;
		if (m_aDelaySeconds.Count() > 128 || (m_aDelaySeconds.Count() > 1 && !m_Spawn.m_sEntityName.IsEmpty()))
			return false;
		foreach (float delay : m_aDelaySeconds)
		{
			if (!(delay >= 0 && delay <= 86400))
				return false;
		}
		return true;
	}

	protected void BeginObserving()
	{
		if (!GC_LobbyGameMode.GetGameMode())
			return;
		GC_LobbyManager.GetMissionStateChanged().Insert(TrySchedule);
		TrySchedule();
	}

	protected void TrySchedule()
	{
		IEntity owner = GetOwner();
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!owner || owner.GetWorld() != GetGame().GetWorld() || !manager || !manager.AreMissionInteractionsActive())
			return;
		if (!m_sRequiredFlag.IsEmpty() && !manager.GetFlag(m_sRequiredFlag))
			return;
		GC_LobbyManager.GetMissionStateChanged().Remove(TrySchedule);
		m_ScheduledManager = manager;
		if (!m_aDelaySeconds || m_aDelaySeconds.IsEmpty())
			GetGame().GetCallqueue().Call(Deliver);
		else
		{
			foreach (float delay : m_aDelaySeconds)
				GetGame().GetCallqueue().CallLater(Deliver, Math.Round(delay * 1000), false);
		}
	}

	protected void Deliver()
	{
		if (m_ScheduledManager && m_ScheduledManager == GC_LobbyManager.GetManager())
			m_ScheduledManager.GC_SpawnFromMissionAnchor(m_Spawn, GetOwner());
	}

	override void OnDelete(IEntity owner)
	{
		GC_LobbyManager.GetMissionStateChanged().Remove(TrySchedule);
		GetGame().GetCallqueue().Remove(BeginObserving);
		GetGame().GetCallqueue().Remove(Deliver);
		super.OnDelete(owner);
	}
}
