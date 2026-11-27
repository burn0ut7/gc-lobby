[BaseContainerProps()]
class GC_OffMapRound
{
	[Attribute("{38BAE094333E31BF}Prefabs/Weapons/Ammo/Ammo_Shell_81mm_HE_M821.et", UIWidgets.ResourceNamePicker)]
	ResourceName m_shellPrefab;
	[Attribute("81mm HE M821")]
	string m_roundName;
	[Attribute("", UIWidgets.Auto, "Authorized factions. Empty allows all factions.")]
	ref array<FactionKey> m_factionKeys;
	[Attribute("", UIWidgets.Auto, "Authorized radio frequencies. Empty allows any frequency.")]
	ref array<int> m_allowedFrequencies;
	[Attribute("16")]
	int m_roundCount;
	[Attribute("1")]
	int m_gunCount;
	[Attribute("90")]
	float m_roundDelay;
	[Attribute("6")]
	float m_roundReload;
	[Attribute("30")]
	float m_roundDispersion;
}

[EntityEditorProps(category: "GameScripted/GC Lobby")]
class GC_OffMapArtilleryClass : GenericEntityClass
{
}

class GC_OffMapArtillery : GenericEntity
{
	[Attribute("", UIWidgets.Object)]
	protected ref array<ref GC_OffMapRound> m_artyRounds;
	protected bool m_bListening;
	static const string ENTITY_NAME = "GC_OffMapArtillery";
	static const string INPUT_ACTION = "GC_OffMapArtilleryMenu";

	//------------------------------------------------------------------------------------------------
	static GC_OffMapArtillery GetActive()
	{
		GC_LobbyGameMode mode = GC_LobbyGameMode.GetGameMode();
		if (!mode || mode.GetState() != SCR_EGameModeState.GAME)
			return null;
		return GC_OffMapArtillery.Cast(mode.GetWorld().FindEntityByName(ENTITY_NAME));
	}

	//------------------------------------------------------------------------------------------------
	void GetAuthorizedRounds(IEntity body, notnull array<int> indices)
	{
		indices.Clear();
		SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(body);
		if (!m_artyRounds || !character || !character.GetCharacterController() || character.GetCharacterController().IsDead()
			|| character.GetCharacterController().IsUnconscious() || !character.m_pFactionComponent)
			return;
		Faction faction = character.m_pFactionComponent.GetAffiliatedFaction();
		SCR_InventoryStorageManagerComponent inventory = SCR_InventoryStorageManagerComponent.Cast(
			character.FindComponent(SCR_InventoryStorageManagerComponent));
		if (!faction || !inventory)
			return;

		array<IEntity> items = {};
		inventory.GetItems(items);
		set<int> frequencies = new set<int>();
		bool usableRadio;
		foreach (IEntity item : items)
		{
			SCR_RadioComponent radio = SCR_RadioComponent.Cast(item.FindComponent(SCR_RadioComponent));
			if (!radio || (radio.GetType() != EGadgetType.RADIO_BACKPACK && radio.GetMode() != EGadgetMode.IN_HAND))
				continue;
			usableRadio = true;
			BaseRadioComponent receiver = BaseRadioComponent.Cast(item.FindComponent(BaseRadioComponent));
			if (!receiver)
				continue;
			for (int index; index < receiver.TransceiversCount(); index++)
			{
				BaseTransceiver transceiver = receiver.GetTransceiver(index);
				if (transceiver)
					frequencies.Insert(transceiver.GetFrequency());
			}
		}
		if (!usableRadio)
			return;

		string factionKey = faction.GetFactionKey();
		foreach (int index, GC_OffMapRound round : m_artyRounds)
		{
			if (!round || (round.m_factionKeys && !round.m_factionKeys.IsEmpty() && !round.m_factionKeys.Contains(factionKey)))
				continue;
			if (!round.m_allowedFrequencies || round.m_allowedFrequencies.IsEmpty())
			{
				indices.Insert(index);
				continue;
			}
			foreach (int frequency : round.m_allowedFrequencies)
			{
				if (!frequencies.Contains(frequency))
					continue;
				indices.Insert(index);
				break;
			}
		}
	}

	//------------------------------------------------------------------------------------------------
	GC_OffMapRound GetRound(int index)
	{
		if (!m_artyRounds || !m_artyRounds.IsIndexValid(index))
			return null;
		return m_artyRounds[index];
	}

	//------------------------------------------------------------------------------------------------
	void RequestFire(IEntity body, int roundIndex, int rounds, int guns, int dispersion, string grid)
	{
		if (!Replication.IsServer() || GetActive() != this)
			return;
		GC_OffMapRound round = GetRound(roundIndex);
		if (!round || rounds < 1 || rounds > round.m_roundCount || guns < 1 || guns > round.m_gunCount
			|| dispersion < 0)
			return;
		array<int> authorized = {};
		GetAuthorizedRounds(body, authorized);
		if (!authorized.Contains(roundIndex))
			return;
		vector target;
		if (!ReadTarget(grid, target))
			return;
		Resource projectile = Resource.Load(round.m_shellPrefab);
		if (!projectile || !projectile.IsValid())
			return;

		SetRemaining(roundIndex, round.m_roundCount - rounds);
		Rpc(SetRemaining, roundIndex, round.m_roundCount);
		FireSalvo(roundIndex, rounds, guns, dispersion, target);
	}

	//------------------------------------------------------------------------------------------------
	protected bool ReadTarget(string grid, out vector target)
	{
		int length = grid.Length();
		if (length < 2 || length > 10 || length % 2 != 0)
			return false;
		string digits = "0123456789";
		for (int index; index < length; index++)
		{
			if (!digits.Contains(grid.Substring(index, 1)))
				return false;
		}
		int half = length / 2;
		float multiplier = Math.Pow(10, 5 - half);
		float east = grid.Substring(0, half).ToInt() * multiplier;
		float north = grid.Substring(half, half).ToInt() * multiplier;
		vector minimum, maximum;
		GetWorld().GetBoundBox(minimum, maximum);
		if (east < minimum[0] || east > maximum[0] || north < minimum[2] || north > maximum[2])
			return false;
		target = Vector(east, GetWorld().GetSurfaceY(east, north), north);
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected void FireSalvo(int roundIndex, int remaining, int guns, int dispersion, vector target)
	{
		if (!Replication.IsServer() || GetActive() != this)
			return;
		GC_OffMapRound round = GetRound(roundIndex);
		int count = Math.Min(guns, remaining);
		float spread = Math.Max(round.m_roundDispersion, dispersion);
		for (int index; index < count; index++)
		{
			float angle = Math.RandomFloat(0, Math.PI2);
			float radius = Math.RandomFloat(0, spread);
			vector impact = target + Vector(Math.Cos(angle) * radius, 0, Math.Sin(angle) * radius);
			float delay = round.m_roundDelay + Math.RandomFloat(0, 5);
			GetGame().GetCallqueue().CallLater(Launch, delay * 1000, false, roundIndex, impact);
		}
		if (remaining > count)
			GetGame().GetCallqueue().CallLater(FireSalvo, round.m_roundReload * 1000, false,
				roundIndex, remaining - count, guns, dispersion, target);
	}

	//------------------------------------------------------------------------------------------------
	protected void Launch(int roundIndex, vector target)
	{
		if (!Replication.IsServer() || GetActive() != this)
			return;
		Resource projectile = Resource.Load(GetRound(roundIndex).m_shellPrefab);
		if (!projectile || !projectile.IsValid())
			return;
		EntitySpawnParams spawn = new EntitySpawnParams();
		spawn.Transform[3] = target + vector.Up * 100;
		SCR_Math3D.LookAt(spawn.Transform[3], target, vector.Up, spawn.Transform);
		IEntity shell = GetGame().SpawnEntityPrefab(projectile, GetWorld(), spawn);
		if (!shell)
			return;
		ProjectileMoveComponent movement = ProjectileMoveComponent.Cast(shell.FindComponent(ProjectileMoveComponent));
		if (movement)
			movement.Launch(-vector.Up, vector.Zero, 0.25, shell, null, null, null, null);
	}

	//------------------------------------------------------------------------------------------------
	[RplRpc(RplChannel.Reliable, RplRcver.Broadcast)]
	protected void SetRemaining(int index, int count)
	{
		GC_OffMapRound round = GetRound(index);
		if (round)
			round.m_roundCount = count;
	}

	//------------------------------------------------------------------------------------------------
	override bool RplSave(ScriptBitWriter writer)
	{
		if (!m_artyRounds)
			return true;
		foreach (GC_OffMapRound round : m_artyRounds)
		{
			int remaining;
			if (round)
				remaining = round.m_roundCount;
			writer.WriteInt(remaining);
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	override bool RplLoad(ScriptBitReader reader)
	{
		if (!m_artyRounds)
			return true;
		foreach (GC_OffMapRound round : m_artyRounds)
		{
			int remaining;
			reader.ReadInt(remaining);
			if (round)
				round.m_roundCount = remaining;
		}
		return true;
	}

	//------------------------------------------------------------------------------------------------
	protected void OpenMenu()
	{
		if (GetActive() != this)
			return;
		GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
		if (!controller)
			return;
		array<int> indices = {};
		GetAuthorizedRounds(controller.GetControlledEntity(), indices);
		MenuManager menus = GetGame().GetMenuManager();
		if (!indices.IsEmpty() && menus && !menus.FindMenuByPreset(ChimeraMenuPreset.GC_OffMapArtilleryMenu))
			menus.OpenMenu(ChimeraMenuPreset.GC_OffMapArtilleryMenu);
	}

	//------------------------------------------------------------------------------------------------
	override protected void EOnInit(IEntity owner)
	{
		if (SCR_Global.IsEditMode() || RplSession.Mode() == RplMode.Dedicated)
			return;
		GetGame().GetInputManager().AddActionListener(INPUT_ACTION, EActionTrigger.DOWN, OpenMenu);
		m_bListening = true;
	}

	//------------------------------------------------------------------------------------------------
	void GC_OffMapArtillery(IEntitySource src, IEntity parent)
	{
		SetEventMask(EntityEvent.INIT);
	}

	//------------------------------------------------------------------------------------------------
	void ~GC_OffMapArtillery()
	{
		if (m_bListening)
			GetGame().GetInputManager().RemoveActionListener(INPUT_ACTION, EActionTrigger.DOWN, OpenMenu);
		GetGame().GetCallqueue().Remove(FireSalvo);
		GetGame().GetCallqueue().Remove(Launch);
	}
}
