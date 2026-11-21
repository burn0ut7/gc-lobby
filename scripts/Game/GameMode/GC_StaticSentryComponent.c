[ComponentEditorProps(category: "GC Lobby/Mission", description: "Keeps this AI sentry at its authored position while native AI selects and engages targets.")]
class GC_StaticSentryComponentClass : ScriptComponentClass
{
}

class GC_StaticSentryComponent : ScriptComponent
{
	protected GC_LobbyGameMode m_Mode;
	protected CharacterControllerComponent m_Controller;
	protected AIControlComponent m_AI;
	protected bool m_bOwnsMovementLock;

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && !SCR_Global.IsEditMode())
			GetGame().GetCallqueue().CallLater(Start, 0);
	}

	protected void Start()
	{
		SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(GetOwner());
		m_Mode = GC_LobbyGameMode.GetGameMode();
		if (!character || !m_Mode || character.GetWorld() != m_Mode.GetWorld() || !Replication.IsServer())
			return;

		m_Controller = character.GetCharacterController();
		m_AI = AIControlComponent.Cast(character.FindComponent(AIControlComponent));
		if (!m_Controller || !m_AI)
			return;

		GetGame().GetCallqueue().CallLater(UpdateMovementLock, 250, true);
		UpdateMovementLock();
	}

	protected void UpdateMovementLock()
	{
		IEntity owner = GetOwner();
		if (!owner || !m_Mode || m_Mode != GC_LobbyGameMode.GetGameMode() || !Replication.IsServer())
		{
			GetGame().GetCallqueue().Remove(UpdateMovementLock);
			ReleaseMovementLock();
			return;
		}

		// Game Master possession and other changes of controller can occur without respawning the character.
		PlayerManager players = GetGame().GetPlayerManager();
		if (!m_AI.IsAIActivated() || !players || players.GetPlayerIdFromControlledEntity(owner) > 0)
		{
			ReleaseMovementLock();
			return;
		}

		if (!m_bOwnsMovementLock && !m_Controller.GetDisableMovementControls())
		{
			m_Controller.SetDisableMovementControls(true);
			m_bOwnsMovementLock = true;
		}
	}

	protected void ReleaseMovementLock()
	{
		if (m_bOwnsMovementLock && m_Controller)
			m_Controller.SetDisableMovementControls(false);
		m_bOwnsMovementLock = false;
	}

	override void OnDelete(IEntity owner)
	{
		GetGame().GetCallqueue().Remove(Start);
		GetGame().GetCallqueue().Remove(UpdateMovementLock);
		ReleaseMovementLock();
		super.OnDelete(owner);
	}
}
