class GC_EditorManagerStagedAccessClass : SCR_EditorManagerEntityClass
{
}

//! Applies the owning mission's staging rule without changing native editor permissions.
class GC_EditorManagerStagedAccess : SCR_EditorManagerEntity
{
	//------------------------------------------------------------------------------------------------
	protected bool IsStagingAccessClosed()
	{
		GC_LobbyFreezeComponent freeze = GC_LobbyFreezeComponent.GetInstance();
		return freeze && freeze.IsLimitedEditorLocked() && IsLimited();
	}

	//------------------------------------------------------------------------------------------------
	override bool CanOpen()
	{
		return super.CanOpen() && !IsStagingAccessClosed();
	}

	//------------------------------------------------------------------------------------------------
	void CloseAtStagingEnd()
	{
		if (Replication.IsServer() && IsOpened() && IsStagingAccessClosed())
			ToggleServer(false);
	}
}
