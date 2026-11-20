[ComponentEditorProps(category: "GC Lobby/AI", description: "Rebuilds AI navigation around this composition after initialization")]
class GC_NavmeshRebuildComponentClass : ScriptComponentClass
{
}

class GC_NavmeshRebuildComponent : ScriptComponent
{
	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && !SCR_Global.IsEditMode())
			GetGame().GetCallqueue().CallLater(Rebuild, 0);
	}

	protected void Rebuild()
	{
		IEntity owner = GetOwner();
		if (!owner || !Replication.IsServer() || SCR_Global.IsEditMode()
			|| !GC_LobbyGameMode.Cast(GetGame().GetGameMode()))
			return;

		SCR_AIWorld aiWorld = SCR_AIWorld.Cast(GetGame().GetAIWorld());
		if (aiWorld)
			aiWorld.RequestNavmeshRebuildEntity(owner);
	}

	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(Rebuild);
		super.OnDelete(owner);
	}
}
