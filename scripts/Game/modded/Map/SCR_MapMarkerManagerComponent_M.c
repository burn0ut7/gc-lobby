modded class SCR_MapMarkerManagerComponent
{
	// Stock disconnect cleanup deletes shared markers and has no retention setting.
	override void OnPlayerDisconnected(int playerId, KickCauseCode cause, int timeout)
	{
		if (GC_MapPlans.GetInstance() && GetOwner() == GC_LobbyGameMode.GetGameMode())
			return;

		super.OnPlayerDisconnected(playerId, cause, timeout);
	}

	//------------------------------------------------------------------------------------------------
	// Ownership is part of the stock JIP snapshot. Existing clients need the delta.
	void GC_SetSharedMarkerOwner(SCR_MapMarkerBase marker, int ownerId)
	{
		marker.SetMarkerOwnerID(ownerId);
		Rpc(GC_DoSetSharedMarkerOwner, marker.GetMarkerID(), ownerId);
	}

	//------------------------------------------------------------------------------------------------
	[RplRpc(RplChannel.Reliable, RplRcver.Broadcast)]
	protected void GC_DoSetSharedMarkerOwner(int markerId, int ownerId)
	{
		if (!GC_LobbyGameMode.GetGameMode() || GetOwner() != GC_LobbyGameMode.GetGameMode())
			return;

		SCR_MapMarkerBase marker = GetStaticMarkerByID(markerId);
		if (!marker)
			marker = GetDisabledMarkerByID(markerId);
		if (marker)
			marker.SetMarkerOwnerID(ownerId);
	}

	//------------------------------------------------------------------------------------------------
	override void OnAddSynchedMarker(SCR_MapMarkerBase marker)
	{
		super.OnAddSynchedMarker(marker);
		GC_MapPlans plans = GC_MapPlans.GetInstance();
		if (!plans)
			return;
		// Native client filtering can remove opposing-faction markers on a listen
		// host. Retain them in the native disabled collection for server/JIP state.
		if (!m_aStaticMarkers.Contains(marker) && !m_aDisabledMarkers.Contains(marker))
		{
			marker.SetServerDisabled(true);
			m_aDisabledMarkers.Insert(marker);
		}
		plans.MarkerAdded(marker);
		plans.Changed();
	}

	//------------------------------------------------------------------------------------------------
	override void OnRemoveSynchedMarker(int markerID)
	{
		super.OnRemoveSynchedMarker(markerID);
		GC_MapPlans plans = GC_MapPlans.GetInstance();
		if (plans)
		{
			plans.MarkerRemoved(markerID);
			plans.Changed();
		}
	}

	//------------------------------------------------------------------------------------------------
	override void InsertStaticMarker(SCR_MapMarkerBase marker, bool isLocal, bool isServerMarker = false)
	{
		// Preserve mission-owned markers and replicated/JIP delivery. Only player
		// insertion (private or shared) is locked when the match starts.
		if ((isLocal || !isServerMarker) && GC_LobbyPhase.ArePlayerMapMarkersLocked())
			return;

		if ((isLocal || !isServerMarker) && GC_LobbyPhase.UsesBriefingLeaderMarkers())
		{
			PlayerController controller = GetGame().GetPlayerController();
			if (!controller || !marker || marker.GetType() != SCR_EMapMarkerType.PLACED_CUSTOM
				|| !GC_LobbyPhase.CanPlacePlayerMapMarker(controller.GetPlayerId()))
				return;
		}

		super.InsertStaticMarker(marker, isLocal, isServerMarker);
	}
}
