[ComponentEditorProps(category: "GC Lobby/Mission", description: "Assigns the scenario reference name to a dynamically instantiated mission entity.")]
class GC_MissionEntityNameComponentClass : ScriptComponentClass
{
}

class GC_MissionEntityNameComponent : ScriptComponent
{
	[Attribute("", UIWidgets.EditBox, "Unique world name used by mission conditions.")]
	protected string m_sMissionName;

	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && GetGame().InPlayMode() && !m_sMissionName.IsEmpty())
			SetEventMask(owner, EntityEvent.INIT);
	}

	override void EOnInit(IEntity owner)
	{
		super.EOnInit(owner);
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!Replication.IsServer() || !mode || mode.GetWorld() != owner.GetWorld())
			return;
		IEntity existing = owner.GetWorld().FindEntityByName(m_sMissionName);
		if (existing && existing != owner)
		{
			Print("GC | Duplicate mission entity name: " + m_sMissionName, LogLevel.ERROR);
			return;
		}
		owner.SetName(m_sMissionName);
	}
}
