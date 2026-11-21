[ComponentEditorProps(category: "GC Lobby/Mission", description: "Freeze players who walk outside authored staging zones")]
class GC_StagingRestrictionZoneManagerComponentClass : SCR_PlayersRestrictionZoneManagerComponentClass
{
}

//! The stock manager owns zone membership, teleport detection and the warning HUD.
class GC_StagingRestrictionZoneManagerComponent : SCR_PlayersRestrictionZoneManagerComponent
{
	override protected void KillPlayerOutOfZone(int playerID, IEntity playerEntity)
	{
		GC_LobbyFreezeComponent freeze = GC_LobbyFreezeComponent.GetInstance();
		if (!freeze || !freeze.HasAuthoredStagingCombatRestrictions() || freeze.GetOwner() != GetOwner())
		{
			super.KillPlayerOutOfZone(playerID, playerEntity);
			return;
		}
		if (playerEntity && freeze.IsAuthoredStagingCombatRestricted())
		{
			GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(m_PlayerManager.GetPlayerController(playerID));
			if (controller)
				controller.GC_SetStagingZoneMovementRestricted(playerEntity);
		}
	}

	//------------------------------------------------------------------------------------------------
	override protected void SetPlayerZoneData(int playerID, IEntity playerEntity, bool inZone, bool inWarningZone,
		ERestrictionZoneWarningType warningType, vector zoneCenter = vector.Zero, float warningRadiusSq = -1, float zoneRadiusSq = -1)
	{
		super.SetPlayerZoneData(playerID, playerEntity, inZone, inWarningZone, warningType, zoneCenter, warningRadiusSq, zoneRadiusSq);
		GC_LobbyFreezeComponent freeze = GC_LobbyFreezeComponent.GetInstance();
		if (!inZone || !freeze || freeze.GetOwner() != GetOwner() || !freeze.IsAuthoredStagingCombatRestricted())
			return;

		GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(m_PlayerManager.GetPlayerController(playerID));
		if (controller)
			controller.GC_SetStagingZoneMovementRestricted(null);
	}
}
