[ComponentEditorProps(category: "GC Lobby/Spawning", description: "Fills selected vehicle compartments with an authored AI crew when the match starts.")]
class GC_VehicleCrewComponentClass : ScriptComponentClass
{
}

class GC_VehicleCrewComponent : ScriptComponent
{
	[Attribute("1", UIWidgets.CheckBox, "Include pilot compartments.")]
	protected bool m_bAllowPilot;

	[Attribute("1", UIWidgets.CheckBox, "Include turret compartments.")]
	protected bool m_bAllowTurret;

	[Attribute("1", UIWidgets.CheckBox, "Include cargo compartments.")]
	protected bool m_bAllowCargo;

	[Attribute("", UIWidgets.ResourceAssignArray, "Character prefabs in native compartment order.", params: "et")]
	protected ref array<ResourceName> m_aCharacters;

	protected GC_LobbyManager m_Manager;

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (Replication.IsServer() && GetGame().InPlayMode())
			GetGame().GetCallqueue().CallLater(ConnectToLobby, 0);
	}

	protected void ConnectToLobby()
	{
		m_Manager = GC_LobbyManager.GetManager();
		if (!m_Manager || !m_aCharacters || m_aCharacters.IsEmpty())
			return;

		m_Manager.m_OnLobbyPhaseChanged.Insert(OnLobbyPhaseChanged);
		OnLobbyPhaseChanged(m_Manager.GetLobbyPhase());
	}

	protected void OnLobbyPhaseChanged(SCR_EGameModeState phase)
	{
		if (phase != SCR_EGameModeState.GAME)
			return;

		m_Manager.m_OnLobbyPhaseChanged.Remove(OnLobbyPhaseChanged);
		GetGame().GetCallqueue().CallLater(SpawnCrew, 0);
	}

	protected void SpawnCrew()
	{
		IEntity owner = GetOwner();
		if (!owner || !Replication.IsServer() || !m_Manager || m_Manager != GC_LobbyManager.GetManager()
			|| m_Manager.GetLobbyPhase() != SCR_EGameModeState.GAME)
			return;

		SCR_BaseCompartmentManagerComponent compartments = SCR_BaseCompartmentManagerComponent.Cast(owner.FindComponent(SCR_BaseCompartmentManagerComponent));
		if (!compartments)
			return;

		array<BaseCompartmentSlot> seats = {};
		compartments.GetCompartments(seats);
		AIGroup group;
		int characterIndex;
		foreach (BaseCompartmentSlot seat : seats)
		{
			if (characterIndex >= m_aCharacters.Count())
				break;
			if (!seat || seat.IsOccupied() || !seat.IsCompartmentAccessible() || !AllowsSeat(seat.GetType()))
				continue;

			seat.SpawnCharacterInCompartment(m_aCharacters[characterIndex], group);
			characterIndex++;
		}
	}

	protected bool AllowsSeat(ECompartmentType type)
	{
		return (m_bAllowPilot && type == ECompartmentType.PILOT)
			|| (m_bAllowTurret && type == ECompartmentType.TURRET)
			|| (m_bAllowCargo && type == ECompartmentType.CARGO);
	}

	override void OnDelete(IEntity owner)
	{
		if (m_Manager)
			m_Manager.m_OnLobbyPhaseChanged.Remove(OnLobbyPhaseChanged);
		GetGame().GetCallqueue().Remove(ConnectToLobby);
		GetGame().GetCallqueue().Remove(SpawnCrew);
		super.OnDelete(owner);
	}
}
