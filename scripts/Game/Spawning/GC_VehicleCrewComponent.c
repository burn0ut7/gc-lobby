[ComponentEditorProps(category: "GC Lobby/Spawning", description: "Fills selected vehicle compartments with an authored AI crew during GC Lobby.")]
class GC_VehicleCrewComponentClass : ScriptComponentClass
{
}

class GC_VehicleCrewComponent : ScriptComponent
{
	[Attribute("0", UIWidgets.CheckBox, "Seat the AI crew as soon as the active lobby initializes, including before Match.")]
	protected bool m_bSpawnBeforeMatch;

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

	[Attribute("0", UIWidgets.CheckBox, "Keep each gunner in its own idle group when the main crew receives waypoint orders.")]
	protected bool m_bSeparateOrderedGunners;

	[Attribute("0", UIWidgets.CheckBox, "Use each eligible compartment's default character instead of the authored crew list.")]
	protected bool m_bUseDefaultCrew;

	[Attribute("", UIWidgets.ResourceAssignArray, "Character prefabs in native compartment order.", params: "et")]
	protected ref array<ResourceName> m_aCharacters;

	[Attribute("", UIWidgets.Auto, "Existing AI waypoint names assigned to the main crew group in order.")]
	protected ref array<string> m_aCrewWaypoints;

	[Attribute("0", UIWidgets.SpinBox, "Seconds after boarding before assigning crew waypoints.", params: "0 86400")]
	protected float m_fCrewWaypointDelay;

	[Attribute("0", UIWidgets.CheckBox, "Assign this route only if the crew group has no existing waypoints.")]
	protected bool m_bAssignInitialCrewOrders;

	[Attribute("", UIWidgets.Object, "Optional ordered crew groups and seat filters. When set, these replace the single roster above.")]
	protected ref array<ref GC_SpawnCrewGroup> m_aCrewGroups;

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
		if (!m_Manager || ((!m_aCrewGroups || m_aCrewGroups.IsEmpty())
			&& !m_bUseDefaultCrew && (!m_aCharacters || m_aCharacters.IsEmpty())))
			return;

		m_Manager.m_OnLobbyPhaseChanged.Insert(OnLobbyPhaseChanged);
		OnLobbyPhaseChanged(m_Manager.GetLobbyPhase());
	}

	protected void OnLobbyPhaseChanged(SCR_EGameModeState phase)
	{
		if (!IsCrewPhase(phase))
			return;

		m_Manager.m_OnLobbyPhaseChanged.Remove(OnLobbyPhaseChanged);
		GetGame().GetCallqueue().CallLater(SpawnCrew, 0);
	}

	protected void SpawnCrew()
	{
		IEntity owner = GetOwner();
		if (!owner || !CanRunCrew())
			return;

		SCR_BaseCompartmentManagerComponent compartments = SCR_BaseCompartmentManagerComponent.Cast(owner.FindComponent(SCR_BaseCompartmentManagerComponent));
		if (!compartments)
			return;

		array<BaseCompartmentSlot> seats = {};
		compartments.GetCompartments(seats);
		if (m_aCrewGroups && !m_aCrewGroups.IsEmpty())
		{
			SpawnStructuredCrew(seats);
			return;
		}
		AIGroup group;
		AIGroup idleGroup;
		bool hasRoute = m_aCrewWaypoints && !m_aCrewWaypoints.IsEmpty();
		bool separateOrderedGunners = m_bSeparateOrderedGunners && hasRoute;
		int characterIndex;
		foreach (BaseCompartmentSlot seat : seats)
		{
			if (!m_bUseDefaultCrew && characterIndex >= m_aCharacters.Count())
				break;
			if (!seat || seat.IsOccupied() || !seat.IsCompartmentAccessible() || !AllowsSeat(seat.GetType()))
				continue;

			ResourceName character;
			if (m_bUseDefaultCrew)
				character = seat.GetDefaultOccupantPrefab();
			else
				character = m_aCharacters[characterIndex++];
			if (character.IsEmpty())
				continue;
			bool turret = seat.GetType() == ECompartmentType.TURRET;
			IEntity occupant;
			if (turret && (m_bShareIdleGunnerGroup || separateOrderedGunners))
			{
				if (separateOrderedGunners)
					idleGroup = null;
				occupant = seat.SpawnCharacterInCompartment(character, idleGroup);
			}
			else
				occupant = seat.SpawnCharacterInCompartment(character, group);
			if (occupant && m_bKeepGunnersSeated && turret)
			{
				SCR_AICombatComponent combat = SCR_AICombatComponent.Cast(occupant.FindComponent(SCR_AICombatComponent));
				if (combat)
					combat.GC_KeepSpawnedGunnerSeated();
			}
		}
		if (group && hasRoute)
		{
			GetGame().GetCallqueue().CallLater(AssignOrders, Math.Round(m_fCrewWaypointDelay * 1000), false,
				group, m_aCrewWaypoints, m_bAssignInitialCrewOrders);
		}
	}

	protected void SpawnStructuredCrew(array<BaseCompartmentSlot> seats)
	{
		if (m_aCrewGroups.Count() > 16 || seats.Count() > 128)
			return;
		foreach (GC_SpawnCrewGroup crew : m_aCrewGroups)
		{
			if (!crew || !crew.IsValid() || !crew.Prepare())
				return;
		}

		GC_SpawnAttempt attempt = new GC_SpawnAttempt();
		attempt.m_bPlacedCrew = true;
		foreach (GC_SpawnCrewGroup crew : m_aCrewGroups)
		{
			if (!crew.Fill(seats, attempt))
			{
				attempt.Rollback();
				return;
			}
		}
		foreach (GC_InitialCrewOrders orders : attempt.GetInitialOrders())
		{
			GetGame().GetCallqueue().CallLater(AssignOrders, Math.Round(orders.m_fDelay * 1000),
				false, orders.m_Group, orders.m_aWaypoints, true, orders.m_bSkipUnavailable);
		}
	}

	protected bool IsCrewPhase(SCR_EGameModeState phase)
	{
		return phase == SCR_EGameModeState.GAME || (m_bSpawnBeforeMatch
			&& (phase == SCR_EGameModeState.PREGAME || phase == SCR_EGameModeState.SLOTTING || phase == SCR_EGameModeState.BRIEFING));
	}

	protected bool CanRunCrew()
	{
		return Replication.IsServer() && m_Manager && m_Manager == GC_LobbyManager.GetManager()
			&& IsCrewPhase(m_Manager.GetLobbyPhase());
	}

	protected void AssignOrders(AIGroup group, array<string> waypoints, bool initialOnly, bool skipUnavailable = false)
	{
		if (!group || !CanRunCrew())
			return;
		if (initialOnly)
		{
			array<AIWaypoint> existing = {};
			group.GetWaypoints(existing);
			if (!existing.IsEmpty())
				return;
		}
		GC_WaypointOrders.Apply(group, waypoints, false, skipUnavailable);
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
