[BaseContainerProps(), BaseContainerCustomTitleField("m_sName")]
class GC_DerivedMissionFlag
{
	[Attribute("", UIWidgets.EditBox, "Name of a flag computed from other mission flags.")]
	string m_sName;

	[Attribute("", UIWidgets.Object, "Boolean expression using flag checks, groups and fixed answers.")]
	ref GC_RuleCondition m_Expression;

	bool IsValid()
	{
		return !m_sName.IsEmpty() && m_Expression && m_Expression.IsValid() && IsFlagExpression(m_Expression);
	}

	protected bool IsFlagExpression(GC_RuleCondition condition)
	{
		if (condition.m_iCheck != 0)
			return condition.m_iCheck == 1 || condition.m_iCheck == 2;
		if (condition.m_aChecks)
		{
			foreach (GC_RuleCondition child : condition.m_aChecks)
			{
				if (!IsFlagExpression(child))
					return false;
			}
		}
		return true;
	}
}

//! Evaluate ordered definitions against a detached snapshot. A changing definition restarts the pass.
class GC_DerivedFlagRuntime
{
	protected ref array<ref GC_DerivedMissionFlag> m_aDefinitions = {};
	protected ref set<string> m_aNames = new set<string>();
	protected static const int MAX_WRITES = 256;

	bool Configure(array<ref GC_DerivedMissionFlag> definitions, map<string, bool> initialFlags)
	{
		m_aDefinitions.Clear();
		m_aNames.Clear();
		if (!definitions)
			return true;
		if (definitions.Count() > 64)
			return false;
		foreach (GC_DerivedMissionFlag flag : definitions)
		{
			if (!flag || !flag.IsValid() || initialFlags.Contains(flag.m_sName) || m_aNames.Contains(flag.m_sName))
				return false;
			m_aNames.Insert(flag.m_sName);
			m_aDefinitions.Insert(flag);
		}
		return true;
	}

	bool Owns(string name)
	{
		return m_aNames.Contains(name);
	}

	bool Evaluate(map<string, bool> flags, map<string, bool> changed)
	{
		if (m_aDefinitions.IsEmpty())
			return true;
		map<string, bool> next = new map<string, bool>();
		foreach (string name, bool value : flags)
			next.Set(name, value);
		// Missing flags read as false. Keep any prior values so self-supporting cycles can persist.
		int index;
		int writes;
		while (index < m_aDefinitions.Count())
		{
			GC_DerivedMissionFlag flag = m_aDefinitions[index];
			bool before = next.Get(flag.m_sName);
			bool answer = flag.m_Expression.Evaluate(next);
			if (answer == before)
			{
				index++;
				continue;
			}
			if (++writes > MAX_WRITES)
				return false;
			next.Set(flag.m_sName, answer);
			index = 0;
		}
		foreach (GC_DerivedMissionFlag result : m_aDefinitions)
		{
			bool value = next.Get(result.m_sName);
			if (value != flags.Get(result.m_sName))
				changed.Set(result.m_sName, value);
		}
		return true;
	}
}
