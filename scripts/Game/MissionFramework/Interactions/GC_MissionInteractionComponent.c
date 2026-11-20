[ComponentEditorProps(category: "GC Lobby/Mission", description: "Reusable mission interaction. Add GC_MissionInteractionAction to an ActionsManager action slot on this same entity. No nested condition lists.")]
class GC_MissionInteractionComponentClass : ScriptComponentClass
{
}

class GC_MissionInteractionComponent : ScriptComponent
{
	[Attribute("Interact", UIWidgets.EditBox, "Text shown in the normal interaction menu, for example Activate radio.")]
	protected string m_sActionName;
	[Attribute("", UIWidgets.Auto, "Optional faction keys allowed to use this interaction. Empty means all factions. Only living, conscious player-controlled characters can use it; spectators cannot.")]
	protected ref array<FactionKey> m_aFactions;
	[Attribute("", UIWidgets.Auto, "Optional Mission Flag that must currently be true. Keep complex requirements on that flag in GC Lobby Manager, not on this object. Empty means no flag requirement. Available during Match only.")]
	protected GC_MissionFlagName m_sRequiredFlag;
	[Attribute("", UIWidgets.Auto, "Optional Mission Flag to call after use. Its own conditions and repeatability still apply; a rejected flag call does not undo the interaction. Leave empty when using Interaction Condition only.")]
	protected GC_MissionFlagName m_sCallFlag;
	[RplProp()]
	protected bool m_bAvailable;
	protected bool m_bUsing;
	protected ref ScriptInvoker m_OnUsed = new ScriptInvoker();

	//------------------------------------------------------------------------------------------------
	string GetActionName()
	{
		return m_sActionName;
	}

	//------------------------------------------------------------------------------------------------
	ScriptInvoker GetOnUsed()
	{
		return m_OnUsed;
	}

	//------------------------------------------------------------------------------------------------
	bool CanUse(IEntity user)
	{
		if (!IsEligibleUser(user))
			return false;
		if (Replication.IsServer())
			RefreshAvailability();
		return m_bAvailable;
	}

	//------------------------------------------------------------------------------------------------
	bool CanShow(IEntity user)
	{
		return CanUse(user);
	}

	//------------------------------------------------------------------------------------------------
	protected bool IsEligibleUser(IEntity user)
	{
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || manager.GetLobbyPhase() != SCR_EGameModeState.GAME || m_bUsing || !GetOwner().FindComponent(RplComponent))
			return false;
		SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(user);
		if (!character || character.FindComponent(GC_LobbySpectatorBodyComponent))
			return false;
		CharacterControllerComponent controller = character.GetCharacterController();
		if (!controller || controller.IsDead() || controller.IsUnconscious())
			return false;
		PlayerManager players = GetGame().GetPlayerManager();
		if (!players)
			return false;
		int playerId = players.GetPlayerIdFromControlledEntity(user);
		if (playerId <= 0 || !players.IsPlayerConnected(playerId))
			return false;
		return !m_aFactions || m_aFactions.IsEmpty() || m_aFactions.Contains(character.GetFactionKey());
	}

	//------------------------------------------------------------------------------------------------
	//! Called only through a native server-verified user action, never a custom client RPC.
	bool TryUse(IEntity user)
	{
		if (!Replication.IsServer() || !CanUse(user))
			return false;
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		m_bUsing = true;
		if (!PerformInteraction(user))
		{
			m_bUsing = false;
			return false;
		}
		m_OnUsed.Invoke();
		manager.NotifyMissionInteraction(m_sCallFlag);
		m_bUsing = false;
		RefreshAvailability();
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected bool PerformInteraction(IEntity user)
	{
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected bool IsAvailable(GC_LobbyManager manager)
	{
		return manager.AreMissionInteractionsActive() && (m_sRequiredFlag.IsEmpty() || manager.GetFlag(m_sRequiredFlag));
	}

	//------------------------------------------------------------------------------------------------
	protected void RefreshAvailability()
	{
		if (!Replication.IsServer())
			return;
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		bool available = manager && !m_sActionName.IsEmpty() && IsAvailable(manager);
		if (available == m_bAvailable)
			return;
		m_bAvailable = available;
		Replication.BumpMe();
	}

	//------------------------------------------------------------------------------------------------
	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (SCR_Global.IsEditMode())
			return;
		if (Replication.IsServer())
		{
			GC_LobbyManager.GetMissionStateChanged().Insert(RefreshAvailability);
			RefreshAvailability();
		}
	}

	//------------------------------------------------------------------------------------------------
	override void OnDelete(IEntity owner)
	{
		GC_LobbyManager.GetMissionStateChanged().Remove(RefreshAvailability);
		super.OnDelete(owner);
	}
}
