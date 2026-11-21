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

	[Attribute("0", UIWidgets.CheckBox, "Keep AI gunners in their turret compartments.")]
	protected bool m_bKeepGunnersSeated;

	[Attribute("0", UIWidgets.CheckBox, "Keep the gunners together in a separate group without waypoint orders.")]
	protected bool m_bShareIdleGunnerGroup;

	[Attribute("", UIWidgets.ResourceAssignArray, "Character prefabs in native compartment order.", params: "et")]
	protected ref array<ResourceName> m_aCharacters;

	[Attribute("", UIWidgets.Auto, "Existing AI waypoint names assigned to the main crew group in order.")]
	protected ref array<string> m_aCrewWaypoints;

	[Attribute("0", UIWidgets.SpinBox, "Seconds after boarding before assigning crew waypoints.", params: "0 86400")]
	protected float m_fCrewWaypointDelay;

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
		if (!owner || !IsActiveMatch())
			return;

		SCR_BaseCompartmentManagerComponent compartments = SCR_BaseCompartmentManagerComponent.Cast(owner.FindComponent(SCR_BaseCompartmentManagerComponent));
		if (!compartments)
			return;

		array<BaseCompartmentSlot> seats = {};
		compartments.GetCompartments(seats);
		AIGroup group;
		AIGroup idleGroup;
		int characterIndex;
		foreach (BaseCompartmentSlot seat : seats)
		{
			if (characterIndex >= m_aCharacters.Count())
				break;
			if (!seat || seat.IsOccupied() || !seat.IsCompartmentAccessible() || !AllowsSeat(seat.GetType()))
				continue;

			bool turret = seat.GetType() == ECompartmentType.TURRET;
			IEntity occupant;
			if (turret && m_bShareIdleGunnerGroup)
				occupant = seat.SpawnCharacterInCompartment(m_aCharacters[characterIndex], idleGroup);
			else
				occupant = seat.SpawnCharacterInCompartment(m_aCharacters[characterIndex], group);
			if (occupant && m_bKeepGunnersSeated && turret)
			{
				SCR_AICombatComponent combat = SCR_AICombatComponent.Cast(occupant.FindComponent(SCR_AICombatComponent));
				if (combat)
					combat.GC_KeepSpawnedGunnerSeated();
			}
			characterIndex++;
		}
		if (group && m_aCrewWaypoints && !m_aCrewWaypoints.IsEmpty())
		{
			GetGame().GetCallqueue().CallLater(AssignOrders, Math.Round(m_fCrewWaypointDelay * 1000), false, group);
		}
	}

	protected bool IsActiveMatch()
	{
		return Replication.IsServer() && m_Manager && m_Manager == GC_LobbyManager.GetManager()
			&& m_Manager.GetLobbyPhase() == SCR_EGameModeState.GAME;
	}

	protected void AssignOrders(AIGroup group)
	{
		if (group && IsActiveMatch())
			GC_WaypointOrders.Apply(group, m_aCrewWaypoints);
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
		GetGame().GetCallqueue().Remove(AssignOrders);
		super.OnDelete(owner);
	}
}
