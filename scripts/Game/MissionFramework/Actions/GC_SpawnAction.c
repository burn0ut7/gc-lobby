//! One action, with only the fields belonging to the selected spawn setup.
[BaseContainerProps(), BaseContainerCustomStringTitleField("Spawn Action")]
class GC_SpawnAction : GC_ActionBase
{
	[Attribute("", UIWidgets.Object, "Choose the prefab, group or vehicle configuration. Spawns once each time this action runs. Use the event's conditions, repeatability and Delay to control when it happens.")]
	ref GC_PrefabSpawn m_Spawn;

	[Attribute("0", UIWidgets.CheckBox, "With Schedule Action enabled, queue this spawn during pregame, slotting or briefing. Once queued it stays due until this world ends.")]
	bool m_bScheduleFromStaging;

	//------------------------------------------------------------------------------------------------
	override GC_RuleAction BuildAction()
	{
		GC_RuleAction action = new GC_RuleAction();
		action.m_eDo = GC_ERuleAction.SPAWN;
		action.m_bScheduleFromStaging = m_bScheduleFromStaging;
		action.m_Spawn = m_Spawn;
		if (m_Spawn)
			action.m_sTextOrName = m_Spawn.m_sLocation;
		return action;
	}
}

//! This parent is also the useful plain-prefab choice. Configurations hold no runtime entities.
[BaseContainerProps(), BaseContainerCustomStringTitleField("Prefab")]
class GC_PrefabSpawn
{
	[Attribute("", UIWidgets.ResourceNamePicker, "Prefab to create. Use the Staging Playable Group configuration for a playable roster.", "et")]
	ResourceName m_sPrefab;
	[Attribute("", UIWidgets.EditComboBox, "Exact name of an existing entity. Its position and direction are used for the spawn. No automatic ground placement or clear-space search is performed.", enums: {ParamEnum("", "", "gc-choices:2:GC_EntityReferenceChoices")})]
	string m_sLocation;
	[Attribute("", UIWidgets.EditBox, "Optional name for the new entity. By default, an existing entity with this name prevents another spawn.")]
	string m_sEntityName;
	[Attribute("0", UIWidgets.CheckBox, "Preserve an authored duplicate entity name for legacy name lookup.")]
	bool m_bAllowDuplicateName;

	//------------------------------------------------------------------------------------------------
	bool IsValid()
	{
		return !m_sLocation.IsEmpty() && IsValidAtAnchor();
	}

	//------------------------------------------------------------------------------------------------
	bool IsValidAtAnchor()
	{
		return !m_sPrefab.IsEmpty() && (!m_bAllowDuplicateName || !m_sEntityName.IsEmpty());
	}

	//------------------------------------------------------------------------------------------------
	protected bool Prepare(IEntitySource source)
	{
		if (!source)
			return false;
		if (source.GetClassName().ToType().IsInherited(SCR_AIGroup))
		{
			bool playable;
			source.Get("m_bPlayable", playable);
			if (playable)
				return false;
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected bool Configure(IEntity entity, GC_SpawnAttempt attempt)
	{
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected bool AcceptsSpawnedEntity(IEntity entity)
	{
		SCR_AIGroup group = SCR_AIGroup.Cast(entity);
		return !group || !group.IsPlayable();
	}

	//------------------------------------------------------------------------------------------------
	//! Called through the manager's current-engine/server authority gate.
	bool Execute(GC_MissionRuleEngine source = null)
	{
		if (m_sLocation.IsEmpty())
			return false;
		return ExecuteAt(GetGame().GetWorld().FindEntityByName(m_sLocation), source);
	}

	//------------------------------------------------------------------------------------------------
	//! Used by the active framework with a directly owned placement.
	bool ExecuteAt(IEntity location, GC_MissionRuleEngine source, bool allowAnchorName = false)
	{
		if (!location || !IsValidAtAnchor())
			return false;
		BaseWorld world = GetGame().GetWorld();
		if (location.GetWorld() != world)
			return false;
		if (!m_sEntityName.IsEmpty() && !m_bAllowDuplicateName)
		{
			IEntity existing = world.FindEntityByName(m_sEntityName);
			if (existing && !(allowAnchorName && m_sEntityName == location.GetName()) && !GC_PhaseEntitySet.AllowsNameReuse(m_sEntityName))
				return false;
		}
		Resource resource = Resource.Load(m_sPrefab);
		if (!resource || !resource.IsValid() || !Prepare(resource.GetResource().ToEntitySource()))
			return false;
		EntitySpawnParams parameters = new EntitySpawnParams();
		parameters.TransformMode = ETransformMode.WORLD;
		location.GetWorldTransform(parameters.Transform);
		IEntity entity = GetGame().SpawnEntityPrefab(resource, world, parameters);
		if (!entity)
			return false;
		GC_SpawnAttempt attempt = new GC_SpawnAttempt();
		attempt.m_RuleSource = source;
		if (!AcceptsSpawnedEntity(entity) || !Configure(entity, attempt))
		{
			attempt.Rollback();
			SCR_EntityHelper.DeleteEntityAndChildren(entity);
			return false;
		}
		if (!m_sEntityName.IsEmpty())
			entity.SetName(m_sEntityName);
		attempt.Commit();
		return true;
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("AI Group")]
class GC_AIGroupSpawn : GC_PrefabSpawn
{
	[Attribute("", UIWidgets.EditComboBox, "Names of existing AI waypoints, in order. Added after the prefab's existing orders. Leave empty to use only the prefab's orders. Choose a non-playable AI group prefab configured to spawn its members.", enums: {ParamEnum("", "", "gc-choices:2:GC_WaypointChoices")})]
	ref array<string> m_aWaypoints;

	//------------------------------------------------------------------------------------------------
	override bool IsValidAtAnchor()
	{
		return super.IsValidAtAnchor() && GC_RuleAction.ValidTargetNames(m_aWaypoints);
	}

	//------------------------------------------------------------------------------------------------
	override protected bool Prepare(IEntitySource source)
	{
		return super.Prepare(source) && source.GetClassName().ToType().IsInherited(AIGroup)
			&& GC_WaypointOrders.Check(m_aWaypoints);
	}

	//------------------------------------------------------------------------------------------------
	override protected bool Configure(IEntity entity, GC_SpawnAttempt attempt)
	{
		AIGroup group = AIGroup.Cast(entity);
		if (!group)
			return false;
		return GC_WaypointOrders.Apply(group, m_aWaypoints);
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Staging Playable Group")]
class GC_StagingPlayableGroupSpawn : GC_PrefabSpawn
{
	//------------------------------------------------------------------------------------------------
	override bool ExecuteAt(IEntity location, GC_MissionRuleEngine source, bool allowAnchorName = false)
	{
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || !manager.GC_HasFrameworkSource(source))
			return false;

		SCR_EGameModeState phase = manager.GetLobbyPhase();
		if (!GC_LobbyPhase.IsValid(phase) || GC_LobbyPhase.IsAtOrAfter(phase, SCR_EGameModeState.GAME))
			return false;

		return super.ExecuteAt(location, source, allowAnchorName);
	}

	//------------------------------------------------------------------------------------------------
	override protected bool Prepare(IEntitySource source)
	{
		if (!source || !source.GetClassName().ToType().IsInherited(SCR_AIGroup))
			return false;

		bool playable;
		source.Get("m_bPlayable", playable);
		return playable;
	}

	//------------------------------------------------------------------------------------------------
	override protected bool AcceptsSpawnedEntity(IEntity entity)
	{
		SCR_AIGroup group = SCR_AIGroup.Cast(entity);
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		return group && group.IsPlayable() && manager && Replication.FindItemId(group) != RplId.Invalid()
			&& group.m_aUnitPrefabSlots && !group.m_aUnitPrefabSlots.IsEmpty()
			&& manager.GetSlotsForGroup(group).Count() == group.m_aUnitPrefabSlots.Count();
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Crewed Vehicle")]
class GC_CrewedVehicleSpawn : GC_PrefabSpawn
{
	[Attribute("", UIWidgets.Object, "Optional crew groups, processed from top to bottom. Empty: fill available seats with the vehicle's default crew in one AI group. Add groups to separate driver/passengers from gunners or give them different waypoints. Each seat is used at most once.")]
	ref array<ref GC_SpawnCrewGroup> m_aCrewGroups;

	[Attribute("0", UIWidgets.CheckBox, "Reserve the next eligible seat when an explicit station roster is exhausted. Later stations cannot fill that seat.")]
	bool m_bReserveNextCrewSeat;

	//------------------------------------------------------------------------------------------------
	override bool IsValidAtAnchor()
	{
		if (!super.IsValidAtAnchor())
			return false;
		if (!m_aCrewGroups)
			return true;
		if (m_aCrewGroups.Count() > 16)
			return false;
		foreach (GC_SpawnCrewGroup crew : m_aCrewGroups)
		{
			if (!crew || !crew.IsValid() || (!m_sEntityName.IsEmpty() && crew.m_sGroupName == m_sEntityName))
				return false;
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	override protected bool Prepare(IEntitySource source)
	{
		if (!super.Prepare(source))
			return false;
		if (m_aCrewGroups)
		{
			foreach (GC_SpawnCrewGroup crew : m_aCrewGroups)
			{
				if (!crew.Prepare())
					return false;
			}
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	override protected bool Configure(IEntity entity, GC_SpawnAttempt attempt)
	{
		SCR_BaseCompartmentManagerComponent manager = SCR_BaseCompartmentManagerComponent.Cast(entity.FindComponent(SCR_BaseCompartmentManagerComponent));
		if (!manager)
			return false;
		array<BaseCompartmentSlot> slots = {};
		manager.GetCompartments(slots);
		if (slots.IsEmpty() || slots.Count() > 128)
			return false;
		attempt.m_bReserveNextCrewSeat = m_bReserveNextCrewSeat;
		if (!m_aCrewGroups || m_aCrewGroups.IsEmpty())
		{
			GC_SpawnCrewGroup defaults = new GC_SpawnCrewGroup();
			return defaults.Fill(slots, attempt) && attempt.HasCrew();
		}
		foreach (GC_SpawnCrewGroup crew : m_aCrewGroups)
		{
			if (!crew.Fill(slots, attempt))
				return false;
		}
		return attempt.HasCrew();
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Crew Station")]
class GC_SpawnCrewStation
{
	[Attribute("1", UIWidgets.CheckBox, "Fill available driver or pilot seats.")]
	bool m_bDriver = true;
	[Attribute("1", UIWidgets.CheckBox, "Fill available gunner seats. For stationary gunners, put them in their own crew group without waypoints.")]
	bool m_bGunners = true;
	[Attribute("1", UIWidgets.CheckBox, "Fill available passenger seats.")]
	bool m_bPassengers = true;
	[Attribute("", UIWidgets.ResourceNamePicker, "Optional characters in the vehicle's seat order. Empty list: use default crew. With a list, a blank entry leaves that seat empty; when the list ends, this group stops filling seats.", "et")]
	ref array<ResourceName> m_aCharacters;
	//------------------------------------------------------------------------------------------------
	bool IsValid()
	{
		return (m_bDriver || m_bGunners || m_bPassengers) && (!m_aCharacters || m_aCharacters.Count() <= 128);
	}

	//------------------------------------------------------------------------------------------------
	bool Prepare()
	{
		if (m_aCharacters)
		{
			foreach (ResourceName name : m_aCharacters)
			{
				if (name.IsEmpty())
					continue;
				Resource resource = Resource.Load(name);
				if (!resource || !resource.IsValid())
					return false;
			}
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	bool FillStation(array<BaseCompartmentSlot> slots, GC_SpawnAttempt attempt, inout AIGroup group,
		bool seated, bool idleGunners, bool shareIdleGroup, inout AIGroup idleGroup, AIGroup existingGroup)
	{
		int characterIndex;
		bool custom = m_aCharacters && !m_aCharacters.IsEmpty();
		bool reserveNext = attempt.m_bPlacedCrew || attempt.m_bReserveNextCrewSeat;
		array<BaseCompartmentSlot> available = {};
		available.Copy(slots);
		foreach (BaseCompartmentSlot slot : available)
		{
			if (custom && characterIndex >= m_aCharacters.Count() && !reserveNext)
				break;
			if (!slot || slot.IsOccupied() || !slot.IsCompartmentAccessible())
				continue;
			ECompartmentType type = slot.GetType();
			if (!((type == ECompartmentType.PILOT && m_bDriver) || (type == ECompartmentType.TURRET && m_bGunners)
				|| (type == ECompartmentType.CARGO && m_bPassengers)))
				continue;
			// Reserve the next eligible seat before moving to the following station.
			if (custom && characterIndex >= m_aCharacters.Count())
			{
				slots.RemoveItemOrdered(slot);
				break;
			}
			ResourceName character;
			if (custom)
				character = m_aCharacters[characterIndex++];
			else
				character = slot.GetDefaultOccupantPrefab();
			if (!custom && character.IsEmpty())
				continue;
			slots.RemoveItemOrdered(slot);
			if (character.IsEmpty() || (attempt.m_bPlacedCrew && custom && m_aCharacters[0].IsEmpty()))
				continue;
			bool idle = idleGunners && type == ECompartmentType.TURRET;
			AIGroup seatGroup;
			if (!idle)
			{
				seatGroup = group;
				if (!seatGroup)
					seatGroup = existingGroup;
			}
			else if (shareIdleGroup)
				seatGroup = idleGroup;
			AIGroup previousGroup = seatGroup;
			IEntity occupant = slot.SpawnCharacterInCompartment(character, seatGroup);
			attempt.Track(occupant, seatGroup, previousGroup);
			if (!occupant || !seatGroup)
				return false;
			if (!idle && existingGroup && seatGroup != existingGroup)
				return false;
			if (!idle)
				group = seatGroup;
			else if (shareIdleGroup)
				idleGroup = seatGroup;
			if (seated && type == ECompartmentType.TURRET)
			{
				SCR_AICombatComponent combat = SCR_AICombatComponent.Cast(occupant.FindComponent(SCR_AICombatComponent));
				if (combat)
					combat.GC_KeepSpawnedGunnerSeated();
			}
		}
		return true;
	}
}

[BaseContainerProps(), BaseContainerCustomStringTitleField("Crew Group")]
class GC_SpawnCrewGroup : GC_SpawnCrewStation
{
	[Attribute("", UIWidgets.EditBox, "Optional world name for this AI group. Subsequent crews with the same name join the existing non-playable group.")]
	string m_sGroupName;
	[Attribute("0", UIWidgets.CheckBox, "Preserve a legacy name shared with an existing spawner anchor. The anchor keeps name lookup priority and each crew forms its own group.")]
	bool m_bNameShadowedByAnchor;
	[Attribute("", UIWidgets.Object, "Optional ordered seat selections using the same AI group. Leave empty to use this group's seat settings.")]
	ref array<ref GC_SpawnCrewStation> m_aStations;
	[Attribute("", UIWidgets.EditComboBox, "Existing AI waypoint names in order. Empty means no added orders.", enums: {ParamEnum("", "", "gc-choices:2:GC_WaypointChoices")})]
	ref array<string> m_aWaypoints;
	[Attribute("0", UIWidgets.EditBox, "Seconds after boarding before assigning this group's orders.", params: "0 86400")]
	float m_fWaypointDelay;
	[Attribute("0", UIWidgets.CheckBox, "Give a group without existing orders the waypoints present when the delay expires. Useful for routes shared by rotating compositions.")]
	bool m_bUseAvailableInitialWaypoints;
	[Attribute("0", UIWidgets.CheckBox, "Keep this group's gunners in their turrets rather than voluntarily dismounting to fight.")]
	bool m_bKeepGunnersSeated;
	[Attribute("0", UIWidgets.CheckBox, "Put each gunner in a separate AI group if waypoints are assigned.")]
	bool m_bSeparateOrderedGunners;
	[Attribute("0", UIWidgets.CheckBox, "Keep the gunners together in a separate group without waypoint orders.")]
	bool m_bShareIdleGunnerGroup;

	override bool IsValid()
	{
		if ((m_bNameShadowedByAnchor && m_sGroupName.IsEmpty()) || m_sGroupName.Length() > 128
			|| !GC_RuleAction.ValidTargetNames(m_aWaypoints)
			|| m_fWaypointDelay < 0 || m_fWaypointDelay > 86400)
			return false;
		if (!m_aStations || m_aStations.IsEmpty())
			return super.IsValid();
		if (m_aStations.Count() > 16)
			return false;
		foreach (GC_SpawnCrewStation station : m_aStations)
		{
			if (!station || !station.IsValid())
				return false;
		}
		return true;
	}

	override bool Prepare()
	{
		if (!m_bUseAvailableInitialWaypoints && !GC_WaypointOrders.Check(m_aWaypoints))
			return false;
		if (!m_aStations || m_aStations.IsEmpty())
			return super.Prepare();
		foreach (GC_SpawnCrewStation station : m_aStations)
		{
			if (!station.Prepare())
				return false;
		}
		return true;
	}

	bool Fill(array<BaseCompartmentSlot> slots, GC_SpawnAttempt attempt)
	{
		AIGroup group;
		AIGroup idleGroup;
		SCR_AIGroup existingGroup;
		IEntity shadowingAnchor;
		if (!m_sGroupName.IsEmpty())
		{
			IEntity existing = GetGame().GetWorld().FindEntityByName(m_sGroupName);
			if (m_bNameShadowedByAnchor)
			{
				if (!existing || existing.Type() != GenericEntity)
					return false;
				shadowingAnchor = existing;
			}
			else if (existing)
			{
				existingGroup = SCR_AIGroup.Cast(existing);
				if ((!existingGroup && !GC_PhaseEntitySet.AllowsNameReuse(m_sGroupName))
					|| (existingGroup && existingGroup.IsPlayable()))
					return false;
			}
		}
		bool idle = m_bShareIdleGunnerGroup || (m_bSeparateOrderedGunners && m_aWaypoints && !m_aWaypoints.IsEmpty());
		if (m_aStations && !m_aStations.IsEmpty())
		{
			foreach (GC_SpawnCrewStation station : m_aStations)
			{
				if (!station.FillStation(slots, attempt, group, m_bKeepGunnersSeated, idle, m_bShareIdleGunnerGroup, idleGroup, existingGroup))
					return false;
			}
		}
		else if (!FillStation(slots, attempt, group, m_bKeepGunnersSeated, idle, m_bShareIdleGunnerGroup, idleGroup, existingGroup))
			return false;
		if (group && !m_sGroupName.IsEmpty())
		{
			SCR_AIGroup namedGroup = SCR_AIGroup.Cast(group);
			if (!namedGroup || namedGroup.IsPlayable())
				return false;
			if (!existingGroup)
				namedGroup.SetName(m_sGroupName);
			if (shadowingAnchor && GetGame().GetWorld().FindEntityByName(m_sGroupName) != shadowingAnchor)
				return false;
			attempt.QueueInitialOrders(group, m_aWaypoints, m_fWaypointDelay, m_bUseAvailableInitialWaypoints);
			return true;
		}
		if (group && (attempt.m_bPlacedCrew || m_bUseAvailableInitialWaypoints))
		{
			attempt.QueueInitialOrders(group, m_aWaypoints, m_fWaypointDelay, m_bUseAvailableInitialWaypoints);
			return true;
		}
		return !group || attempt.AssignOrders(group, m_aWaypoints, m_fWaypointDelay);
	}
}

class GC_InitialCrewOrders
{
	AIGroup m_Group;
	ref array<string> m_aWaypoints;
	float m_fDelay;
	bool m_bSkipUnavailable;
}

//! Local attempt bookkeeping only; native groups own members after success.
class GC_SpawnAttempt
{
	GC_MissionRuleEngine m_RuleSource;
	bool m_bPlacedCrew;
	bool m_bReserveNextCrewSeat;
	protected ref array<IEntity> m_aCrew = {};
	protected ref array<AIGroup> m_aGroups = {};
	protected ref array<ref GC_InitialCrewOrders> m_aInitialOrders = {};

	//------------------------------------------------------------------------------------------------
	void Track(IEntity crew, AIGroup group, AIGroup previousGroup)
	{
		if (crew)
			m_aCrew.Insert(crew);
		if (group && group != previousGroup && !m_aGroups.Contains(group))
			m_aGroups.Insert(group);
	}

	//------------------------------------------------------------------------------------------------
	bool HasCrew()
	{
		return !m_aCrew.IsEmpty();
	}

	bool AssignOrders(AIGroup group, array<string> waypoints, float delay)
	{
		if (!waypoints || waypoints.IsEmpty())
			return true;
		if (delay == 0)
			return GC_WaypointOrders.Apply(group, waypoints);

		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || !manager.GC_HasFrameworkSource(m_RuleSource))
			return false;

		GetGame().GetCallqueue().CallLater(ApplyDelayedOrders, Math.Round(delay * 1000), false,
			manager, m_RuleSource, group, waypoints, false);
		return true;
	}

	void QueueInitialOrders(AIGroup group, array<string> waypoints, float delay, bool skipUnavailable = false)
	{
		if (!waypoints || waypoints.IsEmpty())
			return;
		GC_InitialCrewOrders orders = new GC_InitialCrewOrders();
		orders.m_Group = group;
		orders.m_aWaypoints = waypoints;
		orders.m_fDelay = delay;
		orders.m_bSkipUnavailable = skipUnavailable;
		m_aInitialOrders.Insert(orders);
	}

	array<ref GC_InitialCrewOrders> GetInitialOrders()
	{
		return m_aInitialOrders;
	}

	void Commit()
	{
		if (m_aInitialOrders.IsEmpty())
			return;
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || !manager.GC_HasFrameworkSource(m_RuleSource))
			return;
		foreach (GC_InitialCrewOrders orders : m_aInitialOrders)
		{
			GetGame().GetCallqueue().CallLater(ApplyDelayedOrders, Math.Round(orders.m_fDelay * 1000), false,
				manager, m_RuleSource, orders.m_Group, orders.m_aWaypoints, true, orders.m_bSkipUnavailable);
		}
	}

	protected static void ApplyDelayedOrders(GC_LobbyManager manager, GC_MissionRuleEngine source, AIGroup group,
		array<string> waypoints, bool initialOnly, bool skipUnavailable = false)
	{
		if (!group || !manager || manager != GC_LobbyManager.GetManager() || !manager.GC_HasFrameworkSource(source)
			|| manager.GetLobbyPhase() != SCR_EGameModeState.GAME)
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

	//------------------------------------------------------------------------------------------------
	void Rollback()
	{
		foreach (IEntity crew : m_aCrew)
		{
			if (crew)
				SCR_EntityHelper.DeleteEntityAndChildren(crew);
		}
		foreach (AIGroup group : m_aGroups)
		{
			if (group)
				SCR_EntityHelper.DeleteEntityAndChildren(group);
		}
	}

}
