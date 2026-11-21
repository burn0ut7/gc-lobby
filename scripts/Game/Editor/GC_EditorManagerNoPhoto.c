class GC_EditorManagerNoPhotoClass : GC_EditorManagerStagedAccessClass
{
}

//! Selected by the world's editor settings when ArmaVision is disabled.
class GC_EditorManagerNoPhoto : GC_EditorManagerStagedAccess
{
	override SCR_EditorModeEntity CreateEditorMode(EEditorMode mode, bool isInit, ResourceName prefab = "")
	{
		if (mode == EEditorMode.PHOTO && GC_LobbyGameMode.GetGameMode())
			return null;

		return super.CreateEditorMode(mode, isInit, prefab);
	}
}
