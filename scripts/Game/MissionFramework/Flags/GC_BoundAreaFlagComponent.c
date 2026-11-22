[ComponentEditorProps(category: "GC Lobby/Mission", description: "Observe this area entity for its lifetime, including when it is spawned as part of a composition.")]
class GC_BoundAreaFlagComponentClass : ScriptComponentClass
{
}

class GC_BoundAreaFlagComponent : ScriptComponent
{
	[Attribute("", UIWidgets.Object, "Configure the flag and condition. The condition's area is bound to this entity.")]
	protected ref GC_AreaFlagObserver m_Observer;

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && GetGame().InPlayMode() && m_Observer)
			GetGame().GetCallqueue().Call(Register);
	}

	protected void Register()
	{
		IEntity owner = GetOwner();
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (owner && manager && !manager.GC_RegisterBoundAreaFlag(m_Observer, owner))
			Print("GC | Mission Framework | Could not bind area flag for " + owner.GetName(), LogLevel.WARNING);
	}

	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(Register);
		super.OnDelete(owner);
	}
}
