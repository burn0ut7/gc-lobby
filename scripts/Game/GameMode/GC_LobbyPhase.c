//! GC Lobby extends the base game-mode phase domain so every base-game consumer
//! observes the same automatically replicated state. Explicit large values
//! avoid collisions with base-game or other add-on enum members.
modded enum SCR_EGameModeState
{
	SLOTTING = 1787356801,
	BRIEFING = 1787356802
}

//! Encodes GC Lobby's legal ordering without storing a second phase value.
class GC_LobbyPhase
{
	//------------------------------------------------------------------------------------------------
	//! Re-query the active mode so shared marker hooks also work across mode changes.
	static bool ArePlayerMapMarkersLocked()
	{
		GC_LobbyGameMode gameMode = GC_LobbyGameMode.GetGameMode();
		return gameMode && IsAtOrAfter(gameMode.GetState(), SCR_EGameModeState.GAME);
	}

	//------------------------------------------------------------------------------------------------
	static bool UsesBriefingLeaderMarkers()
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		return mode && mode.HasBriefingLeaderMarkers();
	}

	//------------------------------------------------------------------------------------------------
	static bool CanPlacePlayerMapMarker(int playerId)
	{
		if (!UsesBriefingLeaderMarkers())
			return !ArePlayerMapMarkersLocked();

		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || manager.GetLobbyPhase() != SCR_EGameModeState.BRIEFING || playerId <= 0)
			return false;
		SCR_AIGroup group = manager.GetPlayerGroup(playerId);
		if (!group || !group.GetPlayerIDs().Contains(playerId))
			return false;

		GC_Slot leader;
		SCR_ECharacterRank leaderRank = SCR_ECharacterRank.INVALID;
		array<int> members = group.GetPlayerIDs();
		foreach (GC_Slot slot : manager.GetSlotsForGroup(group))
		{
			int occupant = slot.GetPlayerId();
			if (occupant <= 0 || !members.Contains(occupant))
				continue;
			SCR_ECharacterRank rank = GetBriefingSlotRank(slot);
			if (!leader || rank > leaderRank)
			{
				leader = slot;
				leaderRank = rank;
			}
		}
		return leader && leader.GetPlayerId() == playerId;
	}

	//------------------------------------------------------------------------------------------------
	//! Selection order breaks equal native ranks before the characters spawn.
	protected static SCR_ECharacterRank GetBriefingSlotRank(GC_Slot slot)
	{
		IEntity character = slot.GetEntity();
		if (character)
			return SCR_CharacterRankComponent.GetCharacterRank(character);

		ResourceName prefab = slot.GetResolvedPrefab();
		if (prefab.IsEmpty())
			prefab = slot.GetAuthoredPrefab();
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return SCR_ECharacterRank.INVALID;
		IEntityComponentSource rankSource = SCR_BaseContainerTools.FindComponentSource(resource, SCR_CharacterRankComponent);
		if (!rankSource)
			return SCR_ECharacterRank.INVALID;
		int rank = SCR_ECharacterRank.PRIVATE;
		rankSource.Get("m_iRank", rank);
		return rank;
	}

	static SCR_EGameModeState GetNext(SCR_EGameModeState phase)
	{
		switch (phase)
		{
			case SCR_EGameModeState.PREGAME: return SCR_EGameModeState.SLOTTING;
			case SCR_EGameModeState.SLOTTING: return SCR_EGameModeState.BRIEFING;
			case SCR_EGameModeState.BRIEFING: return SCR_EGameModeState.GAME;
			case SCR_EGameModeState.GAME: return SCR_EGameModeState.POSTGAME;
		}

		return -1;
	}

	static bool IsValid(SCR_EGameModeState phase)
	{
		return phase == SCR_EGameModeState.PREGAME
			|| phase == SCR_EGameModeState.SLOTTING
			|| phase == SCR_EGameModeState.BRIEFING
			|| phase == SCR_EGameModeState.GAME
			|| phase == SCR_EGameModeState.POSTGAME;
	}

	static bool IsAtOrAfter(SCR_EGameModeState phase, SCR_EGameModeState threshold)
	{
		if (!IsValid(phase) || !IsValid(threshold))
			return false;

		return GetOrdinal(phase) >= GetOrdinal(threshold);
	}

	static string GetName(SCR_EGameModeState phase)
	{
		switch (phase)
		{
			case SCR_EGameModeState.PREGAME: return "PREGAME";
			case SCR_EGameModeState.SLOTTING: return "SLOTTING";
			case SCR_EGameModeState.BRIEFING: return "BRIEFING";
			case SCR_EGameModeState.GAME: return "GAME";
			case SCR_EGameModeState.POSTGAME: return "POSTGAME";
		}

		return "UNKNOWN";
	}

	//! Explicit ordering is required because modded enum storage values are
	//! intentionally non-contiguous and must never define lifecycle order.
	static int GetOrdinal(SCR_EGameModeState phase)
	{
		switch (phase)
		{
			case SCR_EGameModeState.PREGAME: return 0;
			case SCR_EGameModeState.SLOTTING: return 1;
			case SCR_EGameModeState.BRIEFING: return 2;
			case SCR_EGameModeState.GAME: return 3;
			case SCR_EGameModeState.POSTGAME: return 4;
		}

		return -1;
	}
}
