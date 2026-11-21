//! Resolve placement before creating the replicated vehicle.
class GC_VehicleSpawnPlacement
{
	static bool Find(IEntity owner, Resource prefab, EntitySpawnParams spawn)
	{
		BaseWorld world = owner.GetWorld();
		vector mins, maxs;
		IEntitySource source = SCR_BaseContainerTools.FindEntitySource(prefab);
		ResourceName mesh;
		for (int i = 0; i < source.GetComponentCount(); i++)
		{
			IEntityComponentSource component = source.GetComponent(i);
			if (component && component.GetClassName() == "MeshObject")
				component.Get("Object", mesh);
		}
		Resource model = Resource.Load(mesh);
		if (!model || !model.IsValid() || !model.GetResource().ToVObject())
			return false;
		IEntity hull = GetGame().SpawnEntity(GenericEntity, world);
		if (!hull)
			return false;
		hull.SetObject(model.GetResource().ToVObject(), "");
		hull.GetBounds(mins, maxs);
		delete hull;
		if (maxs[0] <= mins[0] || maxs[1] <= mins[1] || maxs[2] <= mins[2])
			return false;

		float halfWidth = Math.Max(Math.AbsFloat(mins[0]), Math.AbsFloat(maxs[0]));
		float halfLength = Math.Max(Math.AbsFloat(mins[2]), Math.AbsFloat(maxs[2]));
		float reach = Vector(halfWidth, 0, halfLength).Length() + 3;
		float yaw = owner.GetYawPitchRoll()[0];
		float directions[8] = {0, 45, -45, 90, -90, 135, -135, 180};
		spawn.TransformMode = ETransformMode.WORLD;
		for (int ring = 0; ring < 2; ring++)
		{
			float distance = Math.Max(12, reach) + ring * Math.Max(10, reach);
			foreach (float offset : directions)
			{
				vector direction = vector.FromYaw(yaw + offset);
				Math3D.AnglesToMatrix(Vector(yaw, 0, 0), spawn.Transform);
				spawn.Transform[3] = owner.GetOrigin() + direction * distance;
				if (Fit(world, mins, maxs, spawn))
					return true;
			}
		}
		return false;
	}

	//------------------------------------------------------------------------------------------------
	protected static bool Fit(BaseWorld world, vector mins, vector maxs, EntitySpawnParams spawn)
	{
		if (!SCR_TerrainHelper.SnapAndOrientToTerrain(spawn.Transform, world) || spawn.Transform[1][1] < 0.94)
			return false;

		if (spawn.Transform[3][1] < world.GetOceanBaseHeight())
			return false;

		// The vehicle origin follows the terrain. The clearance box tests nearby objects only.
		TraceOBB box = new TraceOBB();
		Math3D.MatrixCopy(spawn.Transform, box.Mat);
		box.Start = spawn.Transform[3];
		box.Flags = TraceFlags.ENTS;
		box.LayerMask = EPhysicsLayerPresets.Projectile;
		box.Mins = mins - Vector(0.5, 0, 0.5);
		box.Maxs = maxs + Vector(0.5, 0.25, 0.5);
		return world.TracePosition(box, null) >= 0 && !box.TraceEnt;
	}
}
