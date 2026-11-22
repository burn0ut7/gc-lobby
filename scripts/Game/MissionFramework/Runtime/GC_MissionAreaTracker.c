//! Shared weak entity reference, resolved at most once per sample while missing.
class GC_AreaEntityReference
{
	string m_sName;
	IEntity m_Entity;
	vector m_vPosition;
	protected bool m_bReportedMissing;

	//------------------------------------------------------------------------------------------------
	void Sample(BaseWorld world)
	{
		if (!m_Entity && world)
			m_Entity = world.FindEntityByName(m_sName);
		if (m_Entity)
			m_vPosition = m_Entity.GetOrigin();
		else if (!m_bReportedMissing && world)
		{
			Print("GC | Mission Framework | Area Condition cannot find '" + m_sName + "'. Retrying on the next scheduled check.", LogLevel.WARNING);
			m_bReportedMissing = true;
		}
	}
}

//! Shared shape cache; no gameplay ownership or spatial queries live on the area entity.
class GC_MissionAreaGeometry
{
	//------------------------------------------------------------------------------------------------
	//! Shared by authoring discovery and runtime. Polygon subclasses include setup zones and AO limits.
	static bool SupportsEntity(IEntity entity)
	{
		if (GC_RadiusAreaEntity.Cast(entity))
			return true;
		PolylineShapeEntity polygon = PolylineShapeEntity.Cast(entity);
		return polygon && polygon.IsClosed();
	}

	ref GC_AreaEntityReference m_Reference;
	protected IEntity m_CachedEntity;
	protected ref array<vector> m_aWorldPoints = {};
	protected vector m_vMinimum;
	protected vector m_vMaximum;
	protected float m_fRadiusSquared;
	protected bool m_bRadius;
	protected bool m_bValid;
	protected bool m_bReportedInvalid;
	protected ref map<IEntity, bool> m_mMembership = new map<IEntity, bool>();

	//------------------------------------------------------------------------------------------------
	bool IsAvailable()
	{
		return m_bValid && m_Reference.m_Entity;
	}

	//------------------------------------------------------------------------------------------------
	void Sample()
	{
		m_mMembership.Clear();
		IEntity entity = m_Reference.m_Entity;
		if (!entity || !SupportsEntity(entity))
		{
			m_bValid = false;
			m_CachedEntity = null;
			return;
		}
		GC_RadiusAreaEntity radius = GC_RadiusAreaEntity.Cast(entity);
		m_bRadius = radius != null;
		if (radius)
		{
			float metres = radius.GetRadius();
			m_bValid = metres >= 0.1 && metres <= 100000;
			m_fRadiusSquared = metres * metres;
		}
		else
		{
			array<vector> points = {};
			GC_AOLimitEntity limit = GC_AOLimitEntity.Cast(entity);
			PolylineShapeEntity polygon = PolylineShapeEntity.Cast(entity);
			if (limit)
				limit.GetMapPolygon(points);
			else if (polygon && polygon.IsClosed())
			{
				polygon.GetPointsPositions(points);
				for (int index = 0; index < points.Count(); index++)
					points[index] = entity.CoordToParent(points[index]);
			}
			if (m_CachedEntity != entity || !GC_MapPolygon.SamePoints(points, m_aWorldPoints))
			{
				m_aWorldPoints.Copy(points);
				BuildPolygon();
			}
		}
		m_CachedEntity = entity;
		if (!m_bValid && !m_bReportedInvalid)
		{
			Print("GC | Mission Framework | Invalid area '" + m_Reference.m_sName + "'. Use Radius Area or a closed, non-intersecting polygon with nonzero horizontal area and 3-128 points.", LogLevel.WARNING);
			m_bReportedInvalid = true;
		}
	}

	//------------------------------------------------------------------------------------------------
	protected void BuildPolygon()
	{
		m_bValid = GC_MapPolygon.IsValid(m_aWorldPoints);
		if (!m_bValid)
			return;
		m_vMinimum = m_aWorldPoints[0];
		m_vMaximum = m_vMinimum;
		foreach (vector point : m_aWorldPoints)
		{
			m_vMinimum[0] = Math.Min(m_vMinimum[0], point[0]);
			m_vMinimum[2] = Math.Min(m_vMinimum[2], point[2]);
			m_vMaximum[0] = Math.Max(m_vMaximum[0], point[0]);
			m_vMaximum[2] = Math.Max(m_vMaximum[2], point[2]);
		}
	}

	//------------------------------------------------------------------------------------------------
	bool ContainsEntity(IEntity entity, vector position)
	{
		bool inside;
		if (m_mMembership.Find(entity, inside))
			return inside;
		inside = Contains(position);
		m_mMembership.Set(entity, inside);
		return inside;
	}

	//------------------------------------------------------------------------------------------------
	bool Contains(vector position)
	{
		if (!IsAvailable())
			return false;
		if (m_bRadius)
			return vector.DistanceSq(m_Reference.m_vPosition, position) <= m_fRadiusSquared;
		if (position[0] < m_vMinimum[0] || position[0] > m_vMaximum[0] || position[2] < m_vMinimum[2] || position[2] > m_vMaximum[2])
			return false;
		return Math2D.IsPointInPolygonXZ(m_aWorldPoints, position);
	}
}

//! Shared counts for a target selection and area; comparison and side stay on the condition.
class GC_MissionAreaObservation
{
	GC_RuleCondition m_Definition;
	ref array<ref GC_AreaEntityReference> m_aTargets = {};
	ref GC_MissionAreaGeometry m_Area;
	bool m_bBoundToEntity;
	bool m_bChanged;
	protected bool m_bAvailable;
	protected int m_iInside;
	protected int m_iTotal;
	protected int m_iLocalTotal;
	protected int m_iSelectedTotal;
	protected bool m_bSampled;

	//------------------------------------------------------------------------------------------------
	bool Matches(GC_RuleCondition other)
	{
		if (m_Definition.m_bLocalFactionShare != other.m_bLocalFactionShare)
			return false;
		if (m_Definition.m_bLocalFactionShare && (m_Definition.m_bShareWholeMission != other.m_bShareWholeMission
			|| !SameKeys(m_Definition.m_aIgnoredFactions, other.m_aIgnoredFactions)))
			return false;
		if (m_Definition.m_bAreaAnywhere != other.m_bAreaAnywhere || m_Definition.m_sAreaName != other.m_sAreaName || m_Definition.m_iAreaTargets != other.m_iAreaTargets)
			return false;
		if (m_Definition.m_bCountMissingAsOutside != other.m_bCountMissingAsOutside || m_Definition.m_bExcludeDestroyedAreaTargets != other.m_bExcludeDestroyedAreaTargets)
			return false;
		array<string> first = m_Definition.m_aAreaNames;
		array<string> second = other.m_aAreaNames;
		if (other.m_iAreaTargets == 1)
		{
			if (m_Definition.m_iAreaWho != other.m_iAreaWho)
				return false;
			first = m_Definition.m_aAreaFactions;
			second = other.m_aAreaFactions;
		}
		int firstCount;
		int secondCount;
		if (first)
			firstCount = first.Count();
		if (second)
			secondCount = second.Count();
		if (firstCount != secondCount)
			return false;
		if (first)
		{
			foreach (string value : first)
			{
				if (!second || !second.Contains(value))
					return false;
			}
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected bool SameKeys(array<string> first, array<string> second)
	{
		int firstCount;
		int secondCount;
		if (first)
			firstCount = first.Count();
		if (second)
			secondCount = second.Count();
		if (firstCount != secondCount)
			return false;
		if (first)
		{
			foreach (string key : first)
			{
				if (!second || !second.Contains(key))
					return false;
			}
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	void Sample(GC_AreaPopulation population)
	{
		bool available = m_Definition.m_bAreaAnywhere || (m_Area && m_Area.IsAvailable());
		int total;
		int inside;
		int localTotal;
		int selectedTotal;
		if (m_Definition.m_iAreaTargets == 0)
		{
			foreach (GC_AreaEntityReference target : m_aTargets)
			{
				if (!target.m_Entity)
				{
					if (m_Definition.m_bCountMissingAsOutside)
						total++;
					else
						available = false;
					continue;
				}
				total++;
				if (m_Definition.m_bExcludeDestroyedAreaTargets)
				{
					SCR_DamageManagerComponent damage = SCR_DamageManagerComponent.GetDamageManager(target.m_Entity);
					if (damage && damage.GetState() == EDamageState.DESTROYED)
						continue;
				}
				if (available && m_Area.ContainsEntity(target.m_Entity, target.m_vPosition))
					inside++;
			}
		}
		else if (!population || !population.IsAvailable())
			available = false;
		else if (available)
		{
			foreach (GC_AreaCharacter character : population.m_aCharacters)
			{
				if ((m_Definition.m_iAreaWho == 0 && !character.m_bPlayer) || (m_Definition.m_iAreaWho == 1 && character.m_bPlayer))
					continue;
				array<string> factions = m_Definition.m_aAreaFactions;
				if (m_Definition.m_bLocalFactionShare)
				{
					if ((!m_Definition.m_bShareWholeMission && character.m_sFaction.IsEmpty())
						|| (m_Definition.m_aIgnoredFactions && m_Definition.m_aIgnoredFactions.Contains(character.m_sFaction)))
						continue;
					bool local = m_Area.ContainsEntity(character.m_Entity, character.m_vPosition);
					total++;
					if (local)
						localTotal++;
					if (factions.Contains(character.m_sFaction))
					{
						selectedTotal++;
						if (local)
							inside++;
					}
					continue;
				}
				if (factions && !factions.IsEmpty() && !factions.Contains(character.m_sFaction))
					continue;
				total++;
				if (m_Definition.m_bAreaAnywhere || m_Area.ContainsEntity(character.m_Entity, character.m_vPosition))
					inside++;
			}
		}
		m_bChanged = !m_bSampled || available != m_bAvailable || inside != m_iInside || total != m_iTotal || localTotal != m_iLocalTotal || selectedTotal != m_iSelectedTotal;
		m_bSampled = true;
		m_bAvailable = available;
		m_iInside = inside;
		m_iTotal = total;
		m_iLocalTotal = localTotal;
		m_iSelectedTotal = selectedTotal;
	}

	//------------------------------------------------------------------------------------------------
	bool IsAvailable()
	{
		if (!m_bAvailable || (!m_Definition.m_bAreaAnywhere && (!m_Area || !m_Area.IsAvailable())))
			return false;
		if (!m_Definition.m_bCountMissingAsOutside)
		{
			foreach (GC_AreaEntityReference target : m_aTargets)
			{
				if (!target.m_Entity)
					return false;
			}
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	bool PassesCondition(GC_RuleCondition check)
	{
		if (!IsAvailable())
			return false;
		if (check.m_bAreaMajority)
			return m_iInside > m_iLocalTotal - m_iInside;
		if (check.m_bLocalFactionShare)
		{
			int selected = m_iInside;
			int local = m_iLocalTotal;
			if (check.m_bAreaOutside)
			{
				selected = m_iSelectedTotal - selected;
				local = m_iTotal - local;
			}
			int denominator = local;
			if (check.m_bShareWholeMission)
				denominator = m_iTotal;
			if (denominator == 0)
				return check.m_bShareAtMost;
			return PassesProportion(selected, denominator, check.m_fAreaProportion, check.m_bShareAtMost, check.m_bAreaWholeNumberQuotient);
		}
		if (check.m_bAreaProportion)
		{
			int count = m_iInside;
			if (check.m_bAreaOutside)
				count = m_iTotal - m_iInside;
			return PassesProportion(count, m_iTotal, check.m_fAreaProportion, check.m_bAreaProportionAtMost, check.m_bAreaWholeNumberQuotient);
		}
		return Passes(check.m_bAreaOutside, check.m_iAreaComparison, check.m_iAreaRequired);
	}

	//------------------------------------------------------------------------------------------------
	bool IsTied()
	{
		return m_iInside == m_iLocalTotal - m_iInside;
	}

	//------------------------------------------------------------------------------------------------
	//! Divide as floats so a matching threshold uses the same representation as the ratio.
	static bool PassesProportion(int count, int total, float required, bool atMost = false, bool wholeNumberQuotient = false)
	{
		if (total <= 0 || count < 0 || count > total || !(required >= 0 && required <= 1))
			return false;
		float fraction = count;
		if (wholeNumberQuotient)
			fraction = count / total;
		else
			fraction /= total;
		if (atMost)
			return fraction <= required;
		return fraction >= required;
	}

	//------------------------------------------------------------------------------------------------
	bool Passes(bool outside, int comparison, int required)
	{
		if (!IsAvailable())
			return false;
		int count = m_iInside;
		if (outside)
			count = m_iTotal - m_iInside;
		if (comparison == 0)
			return count > required;
		if (comparison == 1)
			return count < required;
		if (comparison == 2)
			return count == required;
		return comparison == 3 && count != required;
	}
}

//! One server-owned sampler. Native registries supply populations; no spawn overrides or spatial queries.
class GC_MissionAreaTracker
{
	protected ref map<GC_RuleCondition, ref GC_AreaCaptureState> m_mCaptures = new map<GC_RuleCondition, ref GC_AreaCaptureState>();
	protected bool m_bCaptureActive;
	protected float m_fSampleTime;
	protected BaseWorld m_World;
	protected GC_LobbyManager m_Manager;
	protected ref GC_AreaPopulation m_Population;
	protected bool m_bIncludeAI;
	protected ref map<string, ref GC_AreaEntityReference> m_mEntities = new map<string, ref GC_AreaEntityReference>();
	protected ref map<string, ref GC_MissionAreaGeometry> m_mAreas = new map<string, ref GC_MissionAreaGeometry>();
	protected ref array<ref GC_MissionAreaObservation> m_aPairs = {};
	protected ref map<GC_RuleCondition, ref GC_MissionAreaObservation> m_mChecks = new map<GC_RuleCondition, ref GC_MissionAreaObservation>();
	protected ref map<GC_RuleCondition, bool> m_mAnswers = new map<GC_RuleCondition, bool>();
	protected ref map<GC_RuleCondition, bool> m_mAvailability = new map<GC_RuleCondition, bool>();

	//------------------------------------------------------------------------------------------------
	void GC_MissionAreaTracker(BaseWorld world, GC_LobbyManager manager = null)
	{
		m_World = world;
		m_Manager = manager;
	}

	//------------------------------------------------------------------------------------------------
	protected GC_AreaEntityReference GetReference(string name)
	{
		GC_AreaEntityReference resolved;
		if (m_mEntities.Find(name, resolved))
			return resolved;
		resolved = new GC_AreaEntityReference();
		resolved.m_sName = name;
		m_mEntities.Set(name, resolved);
		return resolved;
	}

	//------------------------------------------------------------------------------------------------
	bool Register(GC_RuleCondition check, IEntity boundArea = null)
	{
		if (!check || !check.IsValid())
			return false;
		if (boundArea && (check.m_bAreaAnywhere || boundArea.GetWorld() != m_World
			|| !GC_MissionAreaGeometry.SupportsEntity(boundArea)))
			return false;
		if (!boundArea)
		{
			foreach (GC_MissionAreaObservation pair : m_aPairs)
			{
				if (!pair.m_bBoundToEntity && pair.Matches(check))
				{
					if (check.m_Capture && !m_mCaptures.Contains(check))
						m_mCaptures.Set(check, new GC_AreaCaptureState());
					m_mChecks.Set(check, pair);
					return true;
				}
			}
		}
		if (m_aPairs.Count() >= 256)
			return false;
		GC_MissionAreaGeometry area;
		if (boundArea)
		{
			area = new GC_MissionAreaGeometry();
			area.m_Reference = new GC_AreaEntityReference();
			area.m_Reference.m_sName = check.m_sAreaName;
			area.m_Reference.m_Entity = boundArea;
		}
		else if (!check.m_bAreaAnywhere && !m_mAreas.Find(check.m_sAreaName, area))
		{
			area = new GC_MissionAreaGeometry();
			area.m_Reference = GetReference(check.m_sAreaName);
			m_mAreas.Set(check.m_sAreaName, area);
		}
		GC_MissionAreaObservation observation = new GC_MissionAreaObservation();
		observation.m_Definition = check;
		if (check.m_iAreaTargets == 0)
		{
			foreach (string name : check.m_aAreaNames)
			{
				GC_AreaEntityReference target;
				if (boundArea)
				{
					target = new GC_AreaEntityReference();
					target.m_sName = name;
				}
				else
					target = GetReference(name);
				if (!observation.m_aTargets.Contains(target))
					observation.m_aTargets.Insert(target);
			}
		}
		else
		{
			if (!m_Population)
				m_Population = new GC_AreaPopulation();
			if (check.m_iAreaWho != 0)
				m_bIncludeAI = true;
		}
		observation.m_Area = area;
		observation.m_bBoundToEntity = boundArea != null;
		m_aPairs.Insert(observation);
		m_mChecks.Set(check, observation);
		if (check.m_Capture && !m_mCaptures.Contains(check))
			m_mCaptures.Set(check, new GC_AreaCaptureState());
		return true;
	}

	//------------------------------------------------------------------------------------------------
	void RemoveBoundCheck(GC_RuleCondition check)
	{
		GC_MissionAreaObservation observation = GetObservation(check);
		if (!observation || !observation.m_bBoundToEntity)
			return;
		m_aPairs.RemoveItemOrdered(observation);
		m_mChecks.Remove(check);
		m_mAnswers.Remove(check);
		m_mAvailability.Remove(check);
		m_mCaptures.Remove(check);
	}

	//------------------------------------------------------------------------------------------------
	GC_MissionAreaObservation GetObservation(GC_RuleCondition check)
	{
		return m_mChecks.Get(check);
	}

	//------------------------------------------------------------------------------------------------
	bool IsAvailable(GC_RuleCondition check)
	{
		GC_MissionAreaObservation observation = GetObservation(check);
		return observation && observation.IsAvailable() && (!check.m_Capture || (m_bCaptureActive && m_mAvailability.Get(check)));
	}

	//------------------------------------------------------------------------------------------------
	bool PassesCondition(GC_RuleCondition check)
	{
		if (!IsAvailable(check))
			return false;
		if (check.m_Capture)
			return m_mCaptures.Get(check).IsCaptured();
		return GetObservation(check).PassesCondition(check);
	}

	//------------------------------------------------------------------------------------------------
	void Sample(array<GC_RuleCondition> changed, bool initial = false, float elapsedSeconds = -1, bool captureActive = true, array<ref GC_RuleAction> messages = null)
	{
		if (elapsedSeconds >= 0)
			m_fSampleTime = elapsedSeconds;
		else if (!initial)
			m_fSampleTime += 1;
		m_bCaptureActive = captureActive;
		foreach (string name, GC_AreaEntityReference resolved : m_mEntities)
			resolved.Sample(m_World);
		if (m_Population)
			m_Population.Sample(m_Manager, m_bIncludeAI);
		foreach (string areaName, GC_MissionAreaGeometry area : m_mAreas)
			area.Sample();
		foreach (GC_MissionAreaObservation pair : m_aPairs)
		{
			if (pair.m_bBoundToEntity)
			{
				if (pair.m_Area.m_Reference.m_Entity)
					pair.m_Area.m_Reference.m_vPosition = pair.m_Area.m_Reference.m_Entity.GetOrigin();
				pair.m_Area.Sample();
				foreach (GC_AreaEntityReference target : pair.m_aTargets)
					target.Sample(m_World);
			}
			pair.Sample(m_Population);
		}
		foreach (GC_RuleCondition check, GC_MissionAreaObservation observation : m_mChecks)
		{
			if (!initial && !observation.m_bChanged && !check.m_Capture)
				continue;
			bool available = observation.IsAvailable();
			bool answer = observation.PassesCondition(check);
			if (check.m_Capture)
			{
				GC_AreaCaptureState capture = m_mCaptures.Get(check);
				available = available && captureActive;
				if (!available)
					capture.Suspend(m_fSampleTime);
				else
				{
					int status = -1;
					if (check.m_bAreaMajority && observation.IsTied())
						capture.Pause(m_fSampleTime);
					else
						status = capture.Sample(check.m_Capture, answer, m_fSampleTime);
					if (status >= 0 && messages && check.m_Capture.m_Messages)
					{
						bool reversed = (check.m_bLocalFactionShare && check.m_bShareAtMost) || check.m_bAreaProportionAtMost;
						GC_RuleAction message = check.m_Capture.m_Messages.BuildMessage(status, check.m_aAreaFactions, reversed);
						if (message)
							messages.Insert(message);
					}
				}
				answer = available && capture.IsCaptured();
			}
			if (initial || !m_mAvailability.Contains(check) || m_mAvailability.Get(check) != available || m_mAnswers.Get(check) != answer)
				changed.Insert(check);
			m_mAvailability.Set(check, available);
			m_mAnswers.Set(check, answer);
		}
	}
}
