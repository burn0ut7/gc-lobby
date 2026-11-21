using System;
using System.IO;
using System.Text.RegularExpressions;
using System.Collections.Generic;

// Read immediate fields of saved Enfusion text containers. Quoted resource
// GUIDs, comments and fields in nested objects do not affect brace depth.
public sealed class GcNativeBlock
{
    static readonly Regex Tokens = new Regex(@"\G(?:\s+|//[^\n]*|/\*[\s\S]*?\*/|""(?:\\[\s\S]|[^""\\])*""|[{}]|[^\s{}""]+)", RegexOptions.Compiled);
    readonly List<string> tokens;
    readonly Dictionary<int, int> ends;
    readonly int first, end;

    GcNativeBlock(List<string> tokens, Dictionary<int, int> ends, int open)
    {
        this.tokens = tokens;
        this.ends = ends;
        first = open + 1;
        end = ends[open];
    }

    public static GcNativeBlock Read(string path, string type)
    {
        return ReadRoot(path, new[] { type });
    }

    public static GcNativeBlock ReadMissionHeader(string path)
    {
        return ReadRoot(path, new[] { "GC_MissionHeader", "GC_ScenarioFrameworkMissionHeader" });
    }

    static GcNativeBlock ReadRoot(string path, string[] types)
    {
        string source = File.ReadAllText(path);
        var tokens = new List<string>();
        var stack = new Stack<int>();
        var ends = new Dictionary<int, int>();
        int pos = 0;
        foreach (Match match in Tokens.Matches(source))
        {
            pos += match.Length;
            string s = match.Value;
            if (Char.IsWhiteSpace(s[0]) || s.StartsWith("//")) continue;
            if (s.StartsWith("/*"))
            {
                if (!s.EndsWith("*/")) throw new InvalidDataException("Unclosed comment in " + path);
                continue;
            }
            if (s == "{") stack.Push(tokens.Count);
            if (s == "}")
            {
                if (stack.Count == 0) throw new InvalidDataException("Unexpected closing brace in " + path);
                ends.Add(stack.Pop(), tokens.Count);
            }
            tokens.Add(s);
        }
        // Inherited root containers can have dependency-bearing fields in the
        // parent. Require a self-contained saved header/project for this reader.
        if (pos != source.Length || stack.Count != 0 || tokens.Count < 3 ||
            Array.IndexOf(types, tokens[0]) < 0 || tokens[1] != "{" || ends[1] != tokens.Count - 1)
            throw new InvalidDataException("Expected self-contained " + string.Join(" or ", types) + " in " + path);
        return new GcNativeBlock(tokens, ends, 1);
    }

    int Field(string name)
    {
        int found = -1;
        for (int i = first; i < end; i++)
        {
            if (tokens[i] == "{") i = ends[i];
            else if (tokens[i] == name)
            {
                if (found >= 0) throw new InvalidDataException("Duplicate field " + name);
                found = i + 1;
            }
        }
        return found;
    }

    static string Value(string s)
    {
        if (s == "{" || s == "}") throw new InvalidDataException("Expected native scalar");
        return s.StartsWith("\"") ? s.Substring(1, s.Length - 2) : s;
    }

    public string Text(string name)
    {
        int i = Field(name);
        return i < 0 ? null : Value(tokens[i]);
    }

    public GcNativeBlock Object(string name, string type)
    {
        int i = Field(name);
        if (i < 0) return null;
        if (type != null && type.Length > 0)
        {
            if (tokens[i++] != type) throw new InvalidDataException("Unexpected type of " + name);
            if (tokens[i].StartsWith("\"")) i++;
        }
        if (tokens[i] != "{") throw new InvalidDataException("Expected container " + name);
        return new GcNativeBlock(tokens, ends, i);
    }

    public string[] Strings(string name)
    {
        var block = Object(name, null);
        var result = new List<string>();
        if (block != null)
            for (int i = block.first; i < block.end; i++) result.Add(Value(tokens[i]));
        return result.ToArray();
    }

    public IEnumerable<GcNativeBlock> Rows(string type)
    {
        for (int i = first; i < end; i++)
        {
            if (tokens[i++] != type) throw new InvalidDataException("Expected " + type);
            if (tokens[i].StartsWith("\"")) i++;
            if (tokens[i] != "{") throw new InvalidDataException("Expected row");
            yield return new GcNativeBlock(tokens, ends, i);
            i = ends[i];
        }
    }
}
