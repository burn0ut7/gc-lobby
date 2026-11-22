[ComponentEditorProps(category: "GC Lobby/World", description: "Keeps this mission entity out of native garbage collection.")]
class GC_GarbageProtectionComponentClass : ScriptComponentClass
{
}

class GC_GarbageProtectionComponent : ScriptComponent
{
	//------------------------------------------------------------------------------------------------
	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && !SCR_Global.IsEditMode())
			GetGame().GetCallqueue().CallLater(ProtectOwner, 0);
	}

	//------------------------------------------------------------------------------------------------
	protected void ProtectOwner()
	{
		IEntity owner = GetOwner();
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!owner || !mode || mode.GetWorld() != owner.GetWorld() || !Replication.IsServer())
			return;

		SCR_GarbageSystem garbage = SCR_GarbageSystem.GetByEntityWorld(owner);
		if (garbage)
			garbage.UpdateBlacklist(owner, true);
	}

	//------------------------------------------------------------------------------------------------
	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(ProtectOwner);
		super.OnDelete(owner);
	}
}
