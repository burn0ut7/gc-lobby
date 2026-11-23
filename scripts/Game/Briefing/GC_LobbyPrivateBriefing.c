modded class GC_LobbyManager
{
	static const int MAX_PRIVATE_BRIEFING_PAGES = 4;
	static const int MAX_PRIVATE_BRIEFING_MARKERS = 16;
	protected static const int MAX_PRIVATE_BRIEFING_CONTENT = 8192;

	//------------------------------------------------------------------------------------------------
	bool IsPrivateBriefingPhase()
	{
		SCR_EGameModeState phase = GetLobbyPhase();
		return phase == SCR_EGameModeState.BRIEFING || phase == SCR_EGameModeState.GAME;
	}

	//------------------------------------------------------------------------------------------------
	//! Replaces the selected player's private pages and markers. Empty arrays clear the snapshot.
	//! Mission scripts own recipient selection and resend on reconnect.
	bool SetPrivateBriefingForPlayer(int playerId, array<ref GC_Briefing> pages, array<ref GC_BriefingMarker> markers)
	{
		if (!Replication.IsServer() || GetManager() != this || !IsPrivateBriefingPhase()
			|| playerId <= 0 || !pages || !markers
			|| pages.Count() > MAX_PRIVATE_BRIEFING_PAGES || markers.Count() > MAX_PRIVATE_BRIEFING_MARKERS)
			return false;

		PlayerManager players = GetGame().GetPlayerManager();
		if (!players || !players.IsPlayerConnected(playerId))
			return false;
		GC_LobbyPlayerController player = GC_LobbyPlayerController.Cast(players.GetPlayerController(playerId));
		if (!player)
			return false;

		array<int> orders = {};
		int contentLength;
		foreach (GC_Briefing page : pages)
		{
			if (!ValidateBriefing(page) || orders.Contains(page.GetOrder()) || GetBriefing(page.GetOrder()))
				return false;
			orders.Insert(page.GetOrder());
			contentLength += page.GetContent().Length();
		}
		if (contentLength > MAX_PRIVATE_BRIEFING_CONTENT)
			return false;

		array<string> names = {};
		foreach (GC_BriefingMarker marker : markers)
		{
			if (!ValidateBriefingMarker(marker) || names.Contains(marker.GetName()) || GetBriefingMarker(marker.GetName()))
				return false;
			names.Insert(marker.GetName());
		}
		return player.GC_DeliverPrivateBriefing(this, pages, markers);
	}

	//------------------------------------------------------------------------------------------------
	override array<ref GC_Briefing> GetBriefingsForPlayer(int playerId)
	{
		array<ref GC_Briefing> visible = super.GetBriefingsForPlayer(playerId);
		GC_LobbyPlayerController player = GetLocalPrivateBriefingController(playerId);
		if (!player)
			return visible;

		Faction faction = GetPlayerFaction(playerId);
		foreach (GC_Briefing page : player.GC_GetPrivateBriefingPages())
		{
			if (!GetBriefing(page.GetOrder()) && page.IsVisibleToFaction(faction, GetLobbyPhase()))
				visible.Insert(page);
		}
		return visible;
	}

	//------------------------------------------------------------------------------------------------
	GC_Briefing GetBriefingForPlayer(int playerId, int order)
	{
		foreach (GC_Briefing page : GetBriefingsForPlayer(playerId))
		{
			if (page.GetOrder() == order)
				return page;
		}
		return null;
	}

	//------------------------------------------------------------------------------------------------
	override array<ref GC_BriefingMarker> GetBriefingMarkersForPlayer(int playerId)
	{
		array<ref GC_BriefingMarker> visible = super.GetBriefingMarkersForPlayer(playerId);
		GC_LobbyPlayerController player = GetLocalPrivateBriefingController(playerId);
		if (!player)
			return visible;

		Faction faction = GetPlayerFaction(playerId);
		foreach (GC_BriefingMarker marker : player.GC_GetPrivateBriefingMarkers())
		{
			if (!GetBriefingMarker(marker.GetName()) && marker.IsVisibleToFaction(faction, GetLobbyPhase()))
				visible.Insert(marker);
		}
		return visible;
	}

	//------------------------------------------------------------------------------------------------
	protected GC_LobbyPlayerController GetLocalPrivateBriefingController(int playerId)
	{
		if (GetManager() != this || !IsPrivateBriefingPhase())
			return null;
		GC_LobbyPlayerController player = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
		if (!player || player.GetPlayerId() != playerId || !player.GC_HasPrivateBriefingFor(this))
			return null;
		return player;
	}
}
