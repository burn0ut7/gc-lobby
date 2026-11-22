modded class SCR_MapMarkerSyncComponent
{
	//------------------------------------------------------------------------------------------------
	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	override protected void RPC_AskAddStaticMarker(SCR_MapMarkerBase markerData)
	{
		// Check server phase before the stock handler assigns IDs, evicts an old
		// marker at the limit, or broadcasts a request queued before GAME.
		if (GC_LobbyPhase.ArePlayerMapMarkersLocked())
			return;

		if (GC_LobbyPhase.UsesBriefingLeaderMarkers())
		{
			PlayerController controller = PlayerController.Cast(GetOwner());
			if (!controller || !markerData || markerData.GetType() != SCR_EMapMarkerType.PLACED_CUSTOM
				|| !GC_LobbyPhase.CanPlacePlayerMapMarker(controller.GetPlayerId()))
				return;
		}

		super.RPC_AskAddStaticMarker(markerData);
	}
}
