[EntityEditorProps(category: "GC Lobby/World", description: "Removes eligible world objects once when this Lobby world finishes loading.")]
class GC_StartupCleanupEntityClass : GenericEntityClass
{
}

class GC_StartupCleanupEntity : GenericEntity
{
	[Attribute("20", UIWidgets.Slider, "Radius in metres. Counts objects whose world bounds intersect the area.", "0 1000 0.1")]
	protected float m_fRadius;
	[Attribute("", UIWidgets.ResourceAssignArray, "Optional prefab filter, including inherited variants.", "et")]
	protected ref array<ResourceName> m_aPrefabs;
	[Attribute("0", UIWidgets.CheckBox, "Exclude listed prefabs instead of selecting them.")]
	protected bool m_bExcludePrefabs;
	[Attribute("0", UIWidgets.CheckBox, "Only select objects with a visual object.")]
	protected bool m_bVisualObjectsOnly;
	[Attribute("1", UIWidgets.CheckBox, "Select hierarchy roots only. Deleting a root also removes its children.")]
	protected bool m_bRootsOnly;
	[Attribute("1", UIWidgets.CheckBox, "Show the cleanup radius in World Editor.")]
	protected bool m_bPreviewShape;

	protected GC_LobbyGameMode m_Mode;
	protected vector m_vCenter;
	protected ref array<IEntity> m_aCandidates = {};
	protected ref set<string> m_aPrefabIds = new set<string>();

	void GC_StartupCleanupEntity(IEntitySource src, IEntity parent)
	{
		if (GetGame().InPlayMode())
			SetEventMask(EntityEvent.INIT);
	}

	override void EOnInit(IEntity owner)
	{
		super.EOnInit(owner);
		m_Mode = GC_LobbyGameMode.GetGameMode();
		if (m_Mode && m_Mode.GetWorld() == GetWorld())
			m_Mode.GetOnWorldPostProcess().Insert(OnWorldPostProcess);
	}

	protected void OnWorldPostProcess(World world)
	{
		if (!m_Mode || world != GetWorld())
			return;
		m_Mode.GetOnWorldPostProcess().Remove(OnWorldPostProcess);
		GetGame().GetCallqueue().Call(ApplyCleanup);
	}

	protected void ApplyCleanup()
	{
		if (!m_Mode || m_Mode != GC_LobbyGameMode.GetGameMode() || m_Mode.GetWorld() != GetWorld())
			return;
		if (!(m_fRadius >= 0 && m_fRadius <= 1000))
			return;

		if (m_aPrefabs)
		{
			foreach (ResourceName prefab : m_aPrefabs)
			{
				if (prefab.IsEmpty())
					continue;
				if (prefab.Length() < 18 || prefab.Substring(0, 1) != "{" || prefab.Substring(17, 1) != "}")
					return;
				m_aPrefabIds.Insert(prefab.Substring(1, 16));
			}
		}

		m_vCenter = GetOrigin();
		vector extent = Vector(m_fRadius, m_fRadius, m_fRadius);
		EQueryEntitiesFlags flags = EQueryEntitiesFlags.ALL;
		if (m_bVisualObjectsOnly)
			flags = EQueryEntitiesFlags.WITH_OBJECT;
		if (!GetWorld().QueryEntitiesByAABB(m_vCenter - extent, m_vCenter + extent, CollectCandidate, null, flags))
			return;

		foreach (IEntity candidate : m_aCandidates)
		{
			if (candidate && !candidate.IsDeleted())
				SCR_EntityHelper.DeleteEntityAndChildren(candidate);
		}
		m_aCandidates.Clear();
		SCR_EntityHelper.DeleteEntityAndChildren(this);
	}

	protected bool CollectCandidate(IEntity entity)
	{
		if (!entity || (m_bRootsOnly && entity.GetParent())
			|| entity.IsInherited(GC_StartupCleanupEntity) || entity.IsInherited(SCR_PrefabDeleterEntity)
			|| entity.FindComponent(GC_StartupCleanupProtectionComponent))
			return true;
		for (IEntity owner = entity; owner; owner = owner.GetParent())
		{
			if (owner == m_Mode)
				return true;
		}
		if (!m_aPrefabIds.IsEmpty() && MatchesPrefab(entity) == m_bExcludePrefabs)
			return true;
		vector mins, maxs;
		entity.GetWorldBounds(mins, maxs);
		float distance;
		if (SCR_Global.IntersectBoxSphere(m_vCenter, m_fRadius, mins, maxs, distance) || distance == 0)
			m_aCandidates.Insert(entity);
		return true;
	}

	protected bool MatchesPrefab(IEntity entity)
	{
		EntityPrefabData data = entity.GetPrefabData();
		if (!data)
			return false;
		for (BaseContainer item = data.GetPrefab(); item; item = item.GetAncestor())
		{
			ResourceName resource = item.GetResourceName();
			if (!resource.IsEmpty() && m_aPrefabIds.Contains(resource.Substring(1, 16)))
				return true;
		}
		return false;
	}

	void ~GC_StartupCleanupEntity()
	{
		if (m_Mode)
			m_Mode.GetOnWorldPostProcess().Remove(OnWorldPostProcess);
		if (GetGame())
			GetGame().GetCallqueue().Remove(ApplyCleanup);
	}

#ifdef WORKBENCH
	override int _WB_GetAfterWorldUpdateSpecs(IEntitySource src)
	{
		return EEntityFrameUpdateSpecs.CALL_ALWAYS;
	}

	override void _WB_AfterWorldUpdate(float timeSlice)
	{
		super._WB_AfterWorldUpdate(timeSlice);
		if (m_bPreviewShape)
			Shape.CreateSphere(COLOR_YELLOW, ShapeFlags.WIREFRAME | ShapeFlags.ONCE, GetOrigin(), m_fRadius);
	}
#endif
}
