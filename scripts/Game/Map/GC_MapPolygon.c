//! Shared horizontal polygon contract for maps, AO limits and mission areas.
class GC_MapPolygon
{
	static bool SamePoints(array<vector> first, array<vector> second)
	{
		if (first.Count() != second.Count())
			return false;
		foreach (int index, vector point : first)
		{
			if (point != second[index])
				return false;
		}
		return true;
	}

	static float Cross(vector a, vector b, vector c)
	{
		return (b[0] - a[0]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[0] - a[0]);
	}

	protected static bool OnSegment(vector a, vector b, vector point)
	{
		return Math.AbsFloat(Cross(a, b, point)) <= 0.00001
			&& point[0] >= Math.Min(a[0], b[0]) && point[0] <= Math.Max(a[0], b[0])
			&& point[2] >= Math.Min(a[2], b[2]) && point[2] <= Math.Max(a[2], b[2]);
	}

	static bool IsValid(array<vector> points, bool allowProperCrossings = false)
	{
		if (!points || points.Count() < 3 || points.Count() > 128)
			return false;
		int count = points.Count();
		float area;
		for (int i = 0; i < count; i++)
		{
			vector a = points[i];
			vector b = points[(i + 1) % count];
			vector next = points[(i + 2) % count];
			float dx = b[0] - a[0];
			float dz = b[2] - a[2];
			if (dx * dx + dz * dz <= 0.00000001)
				return false;
			// Adjacent edges may share a vertex, but may not double back over each other.
			if (Math.AbsFloat(Cross(a, b, next)) <= 0.00001
				&& dx * (next[0] - b[0]) + dz * (next[2] - b[2]) < 0)
				return false;
			area += Cross(points[0], a, b);
			for (int j = i + 1; j < count; j++)
			{
				if (j == i + 1 || (i == 0 && j == count - 1))
					continue;
				vector c = points[j];
				vector d = points[(j + 1) % count];
				float abC = Cross(a, b, c);
				float abD = Cross(a, b, d);
				float cdA = Cross(c, d, a);
				float cdB = Cross(c, d, b);
				if (!allowProperCrossings && OppositeSides(abC, abD) && OppositeSides(cdA, cdB))
					return false;
				if (OnSegment(a, b, c) || OnSegment(a, b, d) || OnSegment(c, d, a) || OnSegment(c, d, b))
					return false;
			}
		}
		return Math.AbsFloat(area) > 0.01;
	}

	protected static bool OppositeSides(float first, float second)
	{
		return (first > 0 && second < 0) || (first < 0 && second > 0);
	}

	//! Add sweep boundaries for the inverted map fill. The AO keeps its original points.
	static bool SplitCrossingEdges(array<vector> points, array<vector> splitPoints)
	{
		int count = points.Count();
		for (int edge = 0; edge < count; edge++)
		{
			vector a = points[edge];
			vector b = points[(edge + 1) % count];
			splitPoints.Insert(a);
			array<float> fractions = {};
			for (int other = 0; other < count; other++)
			{
				vector c = points[other];
				vector d = points[(other + 1) % count];
				float first = Cross(c, d, a);
				float second = Cross(c, d, b);
				if (OppositeSides(first, second) && OppositeSides(Cross(a, b, c), Cross(a, b, d)))
					fractions.Insert(first / (first - second));
			}
			if (splitPoints.Count() + fractions.Count() > 128)
				return false;
			fractions.Sort();
			foreach (float fraction : fractions)
				splitPoints.Insert(a + (b - a) * fraction);
		}
		return true;
	}
}
