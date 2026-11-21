//! A mission can defer slotting and briefing until its own setup has published.
class GC_LobbySetupGateComponentClass : SCR_BaseGameModeComponentClass
{
}

class GC_LobbySetupGateComponent : SCR_BaseGameModeComponent
{
	bool IsLobbySetupReady()
	{
		return true;
	}
}
