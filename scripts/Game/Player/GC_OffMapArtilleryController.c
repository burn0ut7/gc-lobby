modded class GC_LobbyPlayerController
{
	//------------------------------------------------------------------------------------------------
	void GC_RequestOffMapArtillery(int shell, int rounds, int guns, int dispersion, string grid)
	{
		if (GetGame().GetPlayerController() != this || !GC_OffMapArtillery.GetActive())
			return;
		if (Replication.IsServer())
			GC_RpcAskOffMapArtillery(shell, rounds, guns, dispersion, grid);
		else
			Rpc(GC_RpcAskOffMapArtillery, shell, rounds, guns, dispersion, grid);
	}

	//------------------------------------------------------------------------------------------------
	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void GC_RpcAskOffMapArtillery(int shell, int rounds, int guns, int dispersion, string grid)
	{
		GC_OffMapArtillery artillery = GC_OffMapArtillery.GetActive();
		if (Replication.IsServer() && artillery)
			artillery.RequestFire(GetControlledEntity(), shell, rounds, guns, dispersion, grid);
	}
}
