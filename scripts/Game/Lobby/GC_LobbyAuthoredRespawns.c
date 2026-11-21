[BaseContainerProps(), BaseContainerCustomStringTitleField("Faction respawns")]
class GC_AuthoredFactionRespawn
{
	[Attribute("", UIWidgets.EditBox, "Faction key.")]
	FactionKey m_sFaction;
	[Attribute("0", UIWidgets.EditBox, "Finite shared ticket budget at Match start.")]
	int m_iTickets;
	[Attribute("0", UIWidgets.CheckBox, "Allow this policy to begin with zero tickets and activate when a mission action grants tickets.")]
	bool m_bAllowDynamicTickets;
	[Attribute("0", UIWidgets.EditBox, "Individual delay or world-aligned wave interval in milliseconds.")]
	int m_iIntervalMs;
	[Attribute("0", UIWidgets.CheckBox, "Align to the next multiple of the interval on the world clock.")]
	bool m_bWave;
	[Attribute("", UIWidgets.EditBox, "Optional named anchor. Its transform is captured when the ticket is spent. Missing anchors fall back to the original slot.")]
	string m_sAnchor;
}

//! A snapshot deliberately bypasses the moving-target policy of manual waves.
class GC_AuthoredRespawnDestination : GC_RespawnLocation
{
	protected vector m_aTransform[4];

	void GC_AuthoredRespawnDestination(IEntity anchor)
	{
		anchor.GetWorldTransform(m_aTransform);
	}

	override bool Resolve(BaseWorld world, GC_Slot slot, int index, out EntitySpawnParams parameters)
	{
		parameters = new EntitySpawnParams();
		parameters.TransformMode = ETransformMode.WORLD;
		Math3D.MatrixCopy(m_aTransform, parameters.Transform);
		return true;
	}
}

class GC_AuthoredRespawnRequest
{
	GC_Slot m_Slot;
	ref GC_RespawnWaveCandidate m_Claim;
	GC_AuthoredFactionRespawn m_Policy;
	ref GC_RespawnLocation m_Destination;
}
