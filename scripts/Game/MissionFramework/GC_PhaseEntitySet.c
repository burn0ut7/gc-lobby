class GC_PhaseEntitySetClass : SCR_BaseGameModeComponentClass
{
}

//! Retains the exact instances observed during a phase.
class GC_PhaseEntitySet : SCR_BaseGameModeComponent
{
	[Attribute("", UIWidgets.Auto, "Temporary names to capture and release for reuse.")]
	protected ref array<string> m_aTemporaryNames;
	[Attribute("", UIWidgets.EditBox, "Prefix for captured instance names.")]
	protected string m_sCapturedPrefix;

	protected ref array<IEntity> m_aCaptured = {};
	protected int m_iCapturedIndex;

	//------------------------------------------------------------------------------------------------
	static GC_PhaseEntitySet GetActive()
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode || !Replication.IsServer() || mode.GetState() != SCR_EGameModeState.GAME)
			return null;
		return GC_PhaseEntitySet.Cast(mode.FindComponent(GC_PhaseEntitySet));
	}

	//------------------------------------------------------------------------------------------------
	static bool AllowsNameReuse(string name)
	{
		GC_PhaseEntitySet captureSet = GetActive();
		return captureSet && captureSet.m_aTemporaryNames && captureSet.m_aTemporaryNames.Contains(name);
	}

	//------------------------------------------------------------------------------------------------
	bool Capture()
	{
		if (GetActive() != this || !m_aTemporaryNames || m_aTemporaryNames.IsEmpty() || m_sCapturedPrefix.IsEmpty())
			return false;
		BaseWorld world = GetOwner().GetWorld();
		foreach (string name : m_aTemporaryNames)
		{
			IEntity entity = world.FindEntityByName(name);
			if (!entity)
				continue;
			// Authored temporary names and captured names occupy distinct namespaces.
			m_iCapturedIndex++;
			entity.SetName(m_sCapturedPrefix + m_iCapturedIndex.ToString());
			m_aCaptured.Insert(entity);
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	bool DeleteCaptured()
	{
		if (GetActive() != this)
			return false;
		foreach (IEntity entity : m_aCaptured)
		{
			if (entity)
				SCR_EntityHelper.DeleteEntityAndChildren(entity);
		}
		m_aCaptured.Clear();
		return true;
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Phase entity capture or cleanup")]
class GC_PhaseEntitySetScript : GC_MissionScript
{
	[Attribute("0", UIWidgets.CheckBox, "Delete captured instances. Unchecked captures the current temporary names.")]
	bool m_bDeleteCaptured;

	//------------------------------------------------------------------------------------------------
	override bool Run(GC_LobbyManager manager)
	{
		GC_PhaseEntitySet captureSet = GC_PhaseEntitySet.GetActive();
		if (!captureSet)
			return false;
		if (m_bDeleteCaptured)
			return captureSet.DeleteCaptured();
		return captureSet.Capture();
	}
}
