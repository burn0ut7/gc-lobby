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

	bool ProvidesMatchEvents()
	{
		return false;
	}

	array<ref GC_EventDefinition> GetMatchEvents()
	{
		return null;
	}
}
