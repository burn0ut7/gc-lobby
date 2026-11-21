[ComponentEditorProps(category: "GC Lobby/Mission", description: "Set initial health for one hit zone in a GC Lobby mission.")]
class GC_InitialHitZoneHealthComponentClass : ScriptComponentClass
{
}

class GC_InitialHitZoneHealthComponent : ScriptComponent
{
	[Attribute("", UIWidgets.EditBox, "Hit zone name. Leave empty for the entity's default damage zone.")]
	protected string m_sHitZoneName;

	[Attribute("1", UIWidgets.Slider, "Initial fraction of health.", "0 1 0.01")]
	protected float m_fInitialHealth;

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && !SCR_Global.IsEditMode() && m_fInitialHealth >= 0 && m_fInitialHealth < 1)
			GetGame().GetCallqueue().CallLater(ApplyInitialHealth, 0);
	}

	protected void ApplyInitialHealth()
	{
		IEntity owner = GetOwner();
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!owner || !mode || owner.GetWorld() != mode.GetWorld() || !Replication.IsServer() || SCR_Global.IsEditMode())
			return;

		SCR_DamageManagerComponent manager = SCR_DamageManagerComponent.Cast(owner.FindComponent(SCR_DamageManagerComponent));
		if (!manager)
			return;

		HitZone zone;
		if (m_sHitZoneName.IsEmpty())
			zone = manager.GetDefaultHitZone();
		else
			zone = manager.GetHitZoneByName(m_sHitZoneName);
		if (zone)
			zone.SetHealthScaled(m_fInitialHealth);
		else
			PrintFormat("GC | Initial hit zone health | Missing zone %1 on %2", m_sHitZoneName, owner.GetName(), level: LogLevel.WARNING);
	}

	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(ApplyInitialHealth);
		super.OnDelete(owner);
	}
}
