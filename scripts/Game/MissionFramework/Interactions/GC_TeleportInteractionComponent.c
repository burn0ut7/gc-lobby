[ComponentEditorProps(category: "GC Lobby/Mission", description: "Teleport the interacting player to a named entity. Use with GC_MissionInteractionAction on the same entity.")]
class GC_TeleportInteractionComponentClass : GC_MissionInteractionComponentClass
{
}

class GC_TeleportInteractionComponent : GC_MissionInteractionComponent
{
	[Attribute("", UIWidgets.EditBox, "World entity name of the destination. The player moves to its current origin when the hold completes.")]
	protected string m_sDestinationName;

	//------------------------------------------------------------------------------------------------
	override bool CanShow(IEntity user)
	{
		return IsEligibleUser(user);
	}

	//------------------------------------------------------------------------------------------------
	override protected bool IsAvailable(GC_LobbyManager manager)
	{
		return m_sRequiredFlag.IsEmpty() || super.IsAvailable(manager);
	}

	//------------------------------------------------------------------------------------------------
	override protected bool PerformInteraction(IEntity user)
	{
		IEntity destination;
		if (!m_sDestinationName.IsEmpty())
			destination = GetOwner().GetWorld().FindEntityByName(m_sDestinationName);
		if (!destination)
		{
			PrintFormat("GC | Teleport interaction | Destination '%1' could not be found", m_sDestinationName, level: LogLevel.ERROR);
			return false;
		}

		int playerId = GetGame().GetPlayerManager().GetPlayerIdFromControlledEntity(user);
		return SCR_Global.TeleportPlayer(playerId, destination.GetOrigin());
	}
}
