[ComponentEditorProps(category: "GC Lobby/Mission", description: "Set a mission flag from the owner's main hit zone.")]
class GC_DestroyedEntityFlagComponentClass : ScriptComponentClass
{
}

class GC_DestroyedEntityFlagComponent : ScriptComponent
{
	[Attribute("", UIWidgets.Auto, "Mission flag controlled by this hit zone. Define it in the GC Lobby Manager.")]
	protected GC_MissionFlagName m_sFlag;

	[Attribute("2", UIWidgets.ComboBox, "Hit-zone state that sets the flag.", enums: ParamEnumArray.FromEnum(EDamageState))]
	protected EDamageState m_eTargetState;

	[Attribute("0", UIWidgets.CheckBox, "Keep listening after the first match and clear the flag when the hit zone leaves the target state.")]
	protected bool m_bTrackChanges;

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
		if (!zone || zone != m_HitZone)
			return;
		IEntity owner = GetOwner();
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		GC_LobbyManager lobby = GC_LobbyManager.GetManager();
		if (!owner || !mode || owner.GetWorld() != mode.GetWorld() || !lobby)
			return;

		bool value = lobby.GetFlag(m_sFlag);
		if (zone.GetDamageState() == m_eTargetState)
		{
			if ((value || lobby.CallFlag(m_sFlag)) && !m_bTrackChanges)
				StopObserving();
		}
		else if (m_bTrackChanges && value)
			lobby.ResetFlag(m_sFlag);
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
