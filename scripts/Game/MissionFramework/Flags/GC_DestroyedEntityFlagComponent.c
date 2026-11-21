[ComponentEditorProps(category: "GC Lobby/Mission", description: "Call a mission flag once when the owner's main hit zone is destroyed.")]
class GC_DestroyedEntityFlagComponentClass : ScriptComponentClass
{
}

class GC_DestroyedEntityFlagComponent : ScriptComponent
{
	[Attribute("", UIWidgets.Auto, "Mission flag called once on destruction. Define it in the GC Lobby Manager.")]
	protected GC_MissionFlagName m_sFlag;

	protected SCR_HitZone m_HitZone;

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && !SCR_Global.IsEditMode() && !m_sFlag.IsEmpty())
			GetGame().GetCallqueue().CallLater(ObserveDamage, 0);
	}

	protected void ObserveDamage()
	{
		IEntity owner = GetOwner();
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!owner || !mode || owner.GetWorld() != mode.GetWorld() || !Replication.IsServer() || SCR_Global.IsEditMode())
			return;

		SCR_DamageManagerComponent damage = SCR_DamageManagerComponent.Cast(owner.FindComponent(SCR_DamageManagerComponent));
		if (!damage)
			return;
		m_HitZone = SCR_HitZone.Cast(damage.GetDefaultHitZone());
		if (!m_HitZone)
			return;
		m_HitZone.GetOnDamageStateChanged().Insert(OnDamageStateChanged);
		OnDamageStateChanged(m_HitZone);
	}

	protected void OnDamageStateChanged(SCR_HitZone zone)
	{
		if (!zone || zone != m_HitZone || zone.GetDamageState() != EDamageState.DESTROYED)
			return;
		IEntity owner = GetOwner();
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		GC_LobbyManager lobby = GC_LobbyManager.GetManager();
		if (owner && mode && owner.GetWorld() == mode.GetWorld() && lobby && lobby.CallFlag(m_sFlag))
			StopObserving();
	}

	protected void StopObserving()
	{
		if (m_HitZone)
			m_HitZone.GetOnDamageStateChanged().Remove(OnDamageStateChanged);
		m_HitZone = null;
	}

	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(ObserveDamage);
		StopObserving();
		super.OnDelete(owner);
	}
}
