//! Authored staging covers stock placed and dynamically spawned hit zones. The
//! engine exposes no cancellable global pre-damage event or mode-level filter.
modded class SCR_HitZone
{
	override float ComputeEffectiveDamage(notnull BaseDamageContext damageContext, bool isDOT)
	{
		GC_LobbyFreezeComponent freeze = GC_LobbyFreezeComponent.GetInstance();
		if (freeze && freeze.IsAuthoredStagingCombatRestricted())
			return 0;

		return super.ComputeEffectiveDamage(damageContext, isDOT);
	}
}
