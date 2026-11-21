//! Mission catalogue settings and generated content evidence use native config serialization.
class GC_MissionHeader : SCR_MissionHeader
{
	[Attribute("", UIWidgets.Object, "Choose the mission type, objectives and equipment. Other details are optional.", "noDetails")]
	ref GC_CatalogMissionMetadata m_GC_Catalog;

	// Legacy serialized field; new missions use the catalogue categories.
	[Attribute("", UIWidgets.Hidden)]
	ref array<string> m_aManualTags;

	[Attribute("", UIWidgets.Hidden)]
	ref GC_GeneratedMissionMetadata m_GC_GeneratedMetadata;
}

//! Opt-in header for missions using native Scenario Framework header overrides.
class GC_ScenarioFrameworkMissionHeader : SCR_MissionHeaderScenarioFramework
{
	[Attribute("", UIWidgets.Object, "Choose the mission type, objectives and equipment. Other details are optional.", "noDetails")]
	ref GC_CatalogMissionMetadata m_GC_Catalog;

	[Attribute("", UIWidgets.Hidden)]
	ref GC_GeneratedMissionMetadata m_GC_GeneratedMetadata;
}

[BaseContainerProps()]
class GC_GeneratedMissionMetadata
{
	[Attribute("1")]
	int m_iSchemaVersion;
	[Attribute()]
	ResourceName m_sWorld;
	[Attribute()]
	string m_sCaptureScope;
	[Attribute()]
	ref array<string> m_aLimitations;
	[Attribute()]
	ref array<ref GC_MissionMetadataSlot> m_aPlayerSlots;
	[Attribute()]
	ref array<int> m_aStartHours;
	[Attribute()]
	bool m_bRandomStartTime;
	[Attribute()]
	bool m_bAllowHeaderTime;
	[Attribute()]
	int m_iEntityCount;
	[Attribute()]
	ref array<string> m_aFactions;
	[Attribute()]
	ref array<string> m_aConfiguredFactions;
	[Attribute()]
	ref array<string> m_aReferencedAddonIds;
	[Attribute()]
	ref array<ref GC_MissionMetadataAsset> m_aAssets;
	[Attribute()]
	ref array<ref GC_MissionMetadataPrefab> m_aPrefabs;
	[Attribute()]
	ref array<ref GC_MissionMetadataAddon> m_aLoadedAddons;
	[Attribute()]
	string m_sResourceCoverage;
	[Attribute()]
	ref array<ref GC_MissionMetadataResource> m_aResourceReferences;
	[Attribute()]
	int m_iUnexpandedResourceCount;
	[Attribute()]
	int m_iOmittedVanillaResourceCount;
}

//! Distinct authored resource reference; origin is its first observed field, not every use.
[BaseContainerProps()]
class GC_MissionMetadataResource
{
	[Attribute()]
	ResourceName m_sResource;
	[Attribute()]
	string m_sOrigin;
	[Attribute()]
	string m_sStatus;
	[Attribute()]
	ref array<string> m_aAddonIds = {};
}

[BaseContainerProps()]
class GC_MissionMetadataAsset
{
	[Attribute()]
	string m_sCategory;
	[Attribute()]
	int m_iCount;
}

[BaseContainerProps()]
class GC_MissionMetadataAddon
{
	[Attribute()]
	string m_sId;
	[Attribute()]
	string m_sGuid;
	[Attribute()]
	string m_sTitle;
	[Attribute()]
	bool m_bVanilla;
	[Attribute()]
	bool m_bReferencedByPlacedPrefab;
	[Attribute()]
	bool m_bReferencedByResource;
}

//! Placed prefab evidence; presence does not imply player availability.
[BaseContainerProps()]
class GC_MissionMetadataPrefab
{
	[Attribute()]
	ResourceName m_sPrefab;
	[Attribute()]
	string m_sClass;
	[Attribute()]
	string m_sFaction;
	[Attribute()]
	string m_sAsset;
	[Attribute()]
	string m_sAssetEvidence;
	[Attribute()]
	int m_iCount;
	[Attribute()]
	ref array<string> m_aAddonIds;

}

[BaseContainerProps()]
class GC_MissionMetadataSlot
{
	[Attribute()]
	string m_sGroup;
	[Attribute()]
	int m_iIndex;
	[Attribute()]
	string m_sFaction;
	[Attribute()]
	ResourceName m_sPrefab;
	[Attribute()]
	string m_sName;
	[Attribute()]
	string m_sLoadoutFaction;
}
