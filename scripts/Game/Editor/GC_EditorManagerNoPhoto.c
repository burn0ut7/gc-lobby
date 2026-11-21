class GC_EditorManagerNoPhotoClass : SCR_EditorManagerEntityClass
{
}

//! Selected by the world's editor settings when ArmaVision is disabled.
class GC_EditorManagerNoPhoto : SCR_EditorManagerEntity
{
	override SCR_EditorModeEntity CreateEditorMode(EEditorMode mode, bool isInit, ResourceName prefab = "")
	{
		if (mode == EEditorMode.PHOTO && GC_LobbyGameMode.GetGameMode())
			return null;

		return super.CreateEditorMode(mode, isInit, prefab);
	}
}
