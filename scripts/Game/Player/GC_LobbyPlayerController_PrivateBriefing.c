modded class GC_LobbyPlayerController
{
	protected GC_LobbyManager m_GC_PrivateBriefingManager;
	protected ref array<ref GC_Briefing> m_aGC_PrivateBriefingPages = {};
	protected ref array<ref GC_BriefingMarker> m_aGC_PrivateBriefingMarkers = {};

	//------------------------------------------------------------------------------------------------
	bool GC_DeliverPrivateBriefing(GC_LobbyManager manager, array<ref GC_Briefing> pages, array<ref GC_BriefingMarker> markers)
	{
		if (!Replication.IsServer() || !manager || manager != GC_LobbyManager.GetManager() || !manager.IsPrivateBriefingPhase())
			return false;

		RplId modeId = Replication.FindItemId(manager.GetOwner());
		if (modeId == RplId.Invalid())
			return false;
		if (GetGame().GetPlayerController() == this)
			GC_RpcDo_PrivateBriefing(modeId, pages, markers);
		else
			Rpc(GC_RpcDo_PrivateBriefing, modeId, pages, markers);
		return true;
	}

	//------------------------------------------------------------------------------------------------
	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void GC_RpcDo_PrivateBriefing(RplId modeId, array<ref GC_Briefing> pages, array<ref GC_BriefingMarker> markers)
	{
		GC_LobbyManager manager = GC_LobbyManager.GetManager();
		if (!manager || GetGame().GetPlayerController() != this || Replication.FindItemId(manager.GetOwner()) != modeId)
			return;
		if (manager.GetLobbyPhase() == SCR_EGameModeState.POSTGAME || !pages || !markers)
			return;

		m_GC_PrivateBriefingManager = manager;
		m_aGC_PrivateBriefingPages.Clear();
		m_aGC_PrivateBriefingMarkers.Clear();
		foreach (GC_Briefing page : pages)
		{
			GC_Briefing snapshot = new GC_Briefing();
			snapshot.CopyFrom(page);
			m_aGC_PrivateBriefingPages.Insert(snapshot);
		}
		foreach (GC_BriefingMarker marker : markers)
		{
			GC_BriefingMarker snapshot = new GC_BriefingMarker();
			snapshot.CopyFrom(marker);
			m_aGC_PrivateBriefingMarkers.Insert(snapshot);
		}
		manager.m_OnBriefingsChanged.Invoke(0);
		manager.m_OnBriefingMarkersChanged.Invoke(0);
	}

	//------------------------------------------------------------------------------------------------
	bool GC_HasPrivateBriefingFor(GC_LobbyManager manager)
	{
		return m_GC_PrivateBriefingManager == manager && manager && GetGame().GetPlayerController() == this;
	}

	//------------------------------------------------------------------------------------------------
	array<ref GC_Briefing> GC_GetPrivateBriefingPages()
	{
		return m_aGC_PrivateBriefingPages;
	}

	//------------------------------------------------------------------------------------------------
	array<ref GC_BriefingMarker> GC_GetPrivateBriefingMarkers()
	{
		return m_aGC_PrivateBriefingMarkers;
	}

	//------------------------------------------------------------------------------------------------
	override void ShutdownLobbyPresentation()
	{
		m_GC_PrivateBriefingManager = null;
		m_aGC_PrivateBriefingPages.Clear();
		m_aGC_PrivateBriefingMarkers.Clear();
		super.ShutdownLobbyPresentation();
	}
}
