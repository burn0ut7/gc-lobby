//! Read-only mission marker interaction matching the native map-marker hover
//! pattern: raise the marker, reveal its label, and show an orange icon glow.
class GC_BriefingMarkerWidgetComponent : SCR_ScriptedWidgetComponent
{
	protected static const int PREVIEW_CAMERA_INDEX = 9;
	protected static GC_BriefingMarkerWidgetComponent s_ActivePreview;
	protected Widget m_wLabel;
	protected ImageWidget m_wGlow;
	protected RenderTargetWidget m_wPreview;
	protected IEntity m_PreviewAnchor;
	protected SCR_PIPCamera m_PreviewCamera;
	protected int m_iDefaultZOrder;
	protected int m_iHoverZOrder = GC_BriefingMarker.DEFAULT_HOVER_Z_ORDER;

	override void HandlerAttached(Widget w)
	{
		super.HandlerAttached(w);
		m_wLabel = w.FindAnyWidget("BriefingMarkerLabel");
		m_wGlow = ImageWidget.Cast(w.FindAnyWidget("BriefingMarkerGlow"));
		m_wPreview = RenderTargetWidget.Cast(w.FindAnyWidget("BriefingMarkerPreview"));
		m_iDefaultZOrder = w.GetZOrder();
		if (m_wLabel)
			m_wLabel.SetVisible(true);
		SetHovered(false);
	}

	override void HandlerDeattached(Widget w)
	{
		StopPreview();
		super.HandlerDeattached(w);
	}

	void SetPreviewAnchor(IEntity anchor)
	{
		m_PreviewAnchor = anchor;
		if (m_PreviewAnchor && m_wLabel)
			m_wLabel.SetVisible(false);
	}

	override bool OnMouseEnter(Widget w, int x, int y)
	{
		SetHovered(true);
		return true;
	}

	void SetDrawOrder(int baseOrder, int hoverOrder)
	{
		m_iDefaultZOrder = baseOrder;
		m_iHoverZOrder = hoverOrder;
		if (m_wRoot)
			m_wRoot.SetZOrder(baseOrder);
	}

	override bool OnMouseLeave(Widget w, Widget enterW, int x, int y)
	{
		SetHovered(false);
		return true;
	}

	override bool OnFocus(Widget w, int x, int y)
	{
		return OnMouseEnter(w, x, y);
	}

	override bool OnFocusLost(Widget w, int x, int y)
	{
		return OnMouseLeave(w, null, x, y);
	}

	protected void SetHovered(bool hovered)
	{
		if (m_PreviewAnchor && m_wLabel)
			m_wLabel.SetVisible(hovered);
		if (hovered)
			StartPreview();
		else
			StopPreview();
		if (m_wGlow)
			m_wGlow.SetVisible(hovered);
		if (m_wRoot)
		{
			if (hovered && m_iHoverZOrder >= 0)
				m_wRoot.SetZOrder(m_iHoverZOrder);
			else
				m_wRoot.SetZOrder(m_iDefaultZOrder);
		}
	}

	protected void StartPreview()
	{
		if (!m_PreviewAnchor || !m_wPreview || m_PreviewCamera || !GC_LobbyManager.GetManager())
			return;
		if (s_ActivePreview)
			s_ActivePreview.StopPreview();

		BaseWorld world = m_PreviewAnchor.GetWorld();
		EntitySpawnParams params = new EntitySpawnParams();
		params.TransformMode = ETransformMode.WORLD;
		m_PreviewAnchor.GetWorldTransform(params.Transform);
		m_PreviewCamera = SCR_PIPCamera.Cast(GetGame().SpawnEntity(SCR_PIPCamera, world, params));
		if (!m_PreviewCamera)
			return;
		s_ActivePreview = this;
		m_PreviewCamera.SetVerticalFOV(90);
		m_PreviewCamera.SetNearPlane(0.1);
		m_PreviewCamera.SetFarPlane(1000);
		m_PreviewCamera.SetCameraIndex(PREVIEW_CAMERA_INDEX);
		m_PreviewCamera.ApplyProps(PREVIEW_CAMERA_INDEX);
		m_wPreview.SetWorld(world, PREVIEW_CAMERA_INDEX);
		m_wPreview.SetResolutionScale(1, 1);
		m_wPreview.SetVisible(true);
	}

	void StopPreview()
	{
		if (m_wPreview)
			m_wPreview.SetVisible(false);
		if (s_ActivePreview == this)
			s_ActivePreview = null;
		if (m_PreviewCamera)
		{
			delete m_PreviewCamera;
			m_PreviewCamera = null;
		}
	}
}

//! One local-controller-owned marker layer for every GC Lobby map view.
class GC_BriefingMarkerLayer
{
	protected static const ResourceName MARKER_LAYOUT = "{6A4E000000000001}UI/layouts/Briefing/Lobby_Briefing_Marker.layout";
	protected static const float BASE_ICON_SIZE = 48;
	protected static const float LABEL_WIDTH = 180;
	protected static const float LABEL_HEIGHT = 30;
	protected static const int LABEL_FONT_SIZE = 18;

	protected GC_LobbyManager m_LobbyManager;
	protected SCR_MapEntity m_MapEntity;
	protected ref array<Widget> m_aMarkerRoots = {};
	protected ref array<ImageWidget> m_aMarkerImages = {};
	protected ref array<ImageWidget> m_aMarkerGlows = {};
	protected ref array<Widget> m_aMarkerLabels = {};
	protected ref array<TextWidget> m_aMarkerDescriptions = {};
	protected ref array<ref GC_BriefingMarker> m_aMarkers = {};

	void Start()
	{
		m_LobbyManager = GC_LobbyManager.GetManager();
		if (m_LobbyManager)
		{
			m_LobbyManager.m_OnBriefingMarkersChanged.Insert(OnMarkersChanged);
			m_LobbyManager.m_OnRegistryReset.Insert(OnRegistryReset);
			m_LobbyManager.m_OnPlayerAffiliationChanged.Insert(OnPlayerAffiliationChanged);
			m_LobbyManager.m_OnLobbyPhaseChanged.Insert(OnPhaseChanged);
		}

		SCR_MapEntity.GetOnMapOpen().Insert(OnMapOpen);
		SCR_MapEntity.GetOnMapClose().Insert(OnMapClose);
		SCR_MapEntity.GetOnMapPan().Insert(OnMapPan);
		SCR_MapEntity.GetOnMapZoom().Insert(OnMapZoom);
		RefreshMarkers();
	}

	void Stop()
	{
		GetGame().GetCallqueue().Remove(RefreshMarkers);
		if (m_LobbyManager)
		{
			m_LobbyManager.m_OnBriefingMarkersChanged.Remove(OnMarkersChanged);
			m_LobbyManager.m_OnRegistryReset.Remove(OnRegistryReset);
			m_LobbyManager.m_OnPlayerAffiliationChanged.Remove(OnPlayerAffiliationChanged);
			m_LobbyManager.m_OnLobbyPhaseChanged.Remove(OnPhaseChanged);
		}

		SCR_MapEntity.GetOnMapOpen().Remove(OnMapOpen);
		SCR_MapEntity.GetOnMapClose().Remove(OnMapClose);
		SCR_MapEntity.GetOnMapPan().Remove(OnMapPan);
		SCR_MapEntity.GetOnMapZoom().Remove(OnMapZoom);
		ClearMarkers();
		m_LobbyManager = null;
		m_MapEntity = null;
	}

	protected void OnMarkersChanged(int revision) { RefreshMarkers(); }
	protected void OnRegistryReset(int revision) { RefreshMarkers(); }
	protected void OnPhaseChanged(SCR_EGameModeState phase) { RefreshMarkers(); }
	protected void OnPlayerAffiliationChanged(int playerId)
	{
		PlayerController playerController = GetGame().GetPlayerController();
		if (playerController && playerController.GetPlayerId() == playerId)
			RefreshMarkers();
	}
	protected void OnMapOpen(MapConfiguration config)
	{
		// Run after native map setup, including overrides without OpenComplete.
		GetGame().GetCallqueue().Remove(RefreshMarkers);
		GetGame().GetCallqueue().CallLater(RefreshMarkers, 0);
	}
	protected void OnMapClose(MapConfiguration config)
	{
		GetGame().GetCallqueue().Remove(RefreshMarkers);
		ClearMarkers();
	}
	protected void OnMapPan(float x, float y, bool isPanStart) { UpdateMarkerTransforms(false); }
	protected void OnMapZoom(float zoom) { UpdateMarkerTransforms(); }

	protected void RefreshMarkers()
	{
		ClearMarkers();
		if (!m_LobbyManager)
			m_LobbyManager = GC_LobbyManager.GetManager();
		if (!m_LobbyManager || GC_LobbyManager.GetManager() != m_LobbyManager)
			return;

		m_MapEntity = SCR_MapEntity.GetMapInstance();
		if (!m_MapEntity || !m_MapEntity.IsOpen())
			return;

		Widget mapFrame;
		if (m_MapEntity.GetMapWidget())
			mapFrame = m_MapEntity.GetMapWidget().GetParent();
		if (!mapFrame)
			return;

		PlayerController playerController = GetGame().GetPlayerController();
		if (!playerController)
			return;

		array<ref GC_BriefingMarker> visibleMarkers = m_LobbyManager.GetBriefingMarkersForPlayer(playerController.GetPlayerId());
		foreach (GC_BriefingMarker marker : visibleMarkers)
		{
			Widget markerRoot = GetGame().GetWorkspace().CreateWidgets(MARKER_LAYOUT, mapFrame);
			if (!markerRoot)
			{
				PrintFormat("GC | Briefing Marker | event=marker-create result=rejected marker=%1 reason=layout", marker.GetName(), level: LogLevel.WARNING);
				continue;
			}

			ImageWidget markerImage = ImageWidget.Cast(markerRoot.FindAnyWidget("BriefingMarkerImage"));
			ImageWidget markerGlow = ImageWidget.Cast(markerRoot.FindAnyWidget("BriefingMarkerGlow"));
			Widget markerLabel = markerRoot.FindAnyWidget("BriefingMarkerLabel");
			TextWidget markerDescription = TextWidget.Cast(markerRoot.FindAnyWidget("BriefingMarkerDescription"));
			if (!markerImage || !markerGlow || !markerLabel || !markerDescription)
			{
				PrintFormat("GC | Briefing Marker | event=marker-create result=rejected marker=%1 reason=widgets root=%2 image=%3 glow=%4 label=%5 description=%6",
					marker.GetName(), markerRoot, markerImage, markerGlow, markerLabel, markerDescription, level: LogLevel.WARNING);
				markerRoot.RemoveFromHierarchy();
				continue;
			}

			bool loaded;
			if (marker.GetImage().EndsWith(".imageset"))
				loaded = markerImage.LoadImageFromSet(0, marker.GetImage(), marker.GetQuadName());
			else
				loaded = markerImage.LoadImageTexture(0, marker.GetImage());

			if (!loaded)
			{
				PrintFormat("GC | Failed to load briefing marker image: marker=%1 resource=%2 quad=%3",
					marker.GetName(), marker.GetImage(), marker.GetQuadName(), level: LogLevel.WARNING);
				markerRoot.RemoveFromHierarchy();
				continue;
			}

			ResourceName highlightImage = marker.GetHighlightImage();
			bool textureHighlight = highlightImage.EndsWith(".edds");
			bool highlightLoaded;
			if (textureHighlight)
				highlightLoaded = markerGlow.LoadImageTexture(0, highlightImage);
			else if (!highlightImage.IsEmpty())
				highlightLoaded = markerGlow.LoadImageFromSet(0, highlightImage, marker.GetQuadName());
			if (!highlightImage.IsEmpty() && !highlightLoaded)
			{
				PrintFormat("GC | Failed to load briefing marker highlight: marker=%1 resource=%2 quad=%3",
					marker.GetName(), highlightImage, marker.GetQuadName(), level: LogLevel.WARNING);
			}

			GC_BriefingMarkerWidgetComponent markerHandler = GC_BriefingMarkerWidgetComponent.Cast(markerRoot.FindHandler(GC_BriefingMarkerWidgetComponent));
			if (!markerHandler)
			{
				markerRoot.SetZOrder(marker.GetZOrder());
				PrintFormat("GC | Briefing Marker | event=marker-create result=warning marker=%1 reason=handler", marker.GetName(), level: LogLevel.WARNING);
			}
			else
			{
				markerHandler.SetDrawOrder(marker.GetZOrder(), marker.GetHoverZOrder());
				IEntity entity = GetGame().GetWorld().FindEntityByName(marker.GetName());
				if (entity)
				{
					GC_BriefingMarkerComponent source = GC_BriefingMarkerComponent.Cast(entity.FindComponent(GC_BriefingMarkerComponent));
					if (source)
						markerHandler.SetPreviewAnchor(source.GetHoverCameraAnchor());
				}
			}

			markerImage.SetRotation(marker.GetRotation());
			markerImage.SetColorInt(marker.GetColor());
			markerGlow.SetRotation(marker.GetRotation());
			if (textureHighlight)
				markerGlow.SetColorInt(marker.GetColor());
			else
				markerGlow.SetColor(Color.FromSRGBA(226, 168, 79, 155));
			markerDescription.SetText(marker.GetDescription());
			markerDescription.SetColorInt(marker.GetTextColor());
			m_aMarkerRoots.Insert(markerRoot);
			m_aMarkerImages.Insert(markerImage);
			m_aMarkerGlows.Insert(markerGlow);
			m_aMarkerLabels.Insert(markerLabel);
			m_aMarkerDescriptions.Insert(markerDescription);
			m_aMarkers.Insert(marker);
		}

		UpdateMarkerTransforms();
	}

	//------------------------------------------------------------------------------------------------
	protected void UpdateMarkerTransforms(bool updateLayout = true)
	{
		if (!m_LobbyManager || GC_LobbyManager.GetManager() != m_LobbyManager || !m_MapEntity || !m_MapEntity.IsOpen())
			return;

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		for (int markerIndex; markerIndex < m_aMarkers.Count(); markerIndex++)
		{
			GC_BriefingMarker marker = m_aMarkers[markerIndex];
			Widget markerRoot = m_aMarkerRoots[markerIndex];
			ImageWidget markerImage = m_aMarkerImages[markerIndex];
			ImageWidget markerGlow = m_aMarkerGlows[markerIndex];
			Widget markerLabel = m_aMarkerLabels[markerIndex];
			TextWidget markerDescription = m_aMarkerDescriptions[markerIndex];
			if (!marker || !markerRoot || !markerImage || !markerGlow || !markerLabel || !markerDescription)
				continue;

			float displayWidth = marker.GetSize();
			float displayHeight = marker.GetSize();
			if (marker.IsWorldScale())
			{
				vector projectedSize = GetProjectedWorldSize(marker, workspace);
				displayWidth = projectedSize[0];
				displayHeight = projectedSize[1];
			}

			float minSize = marker.GetMinSize();
			if (minSize > 0)
			{
				if (displayWidth < minSize) displayWidth = minSize;
				if (displayHeight < minSize) displayHeight = minSize;
			}

			int imageWidth;
			int imageHeight;
			markerImage.GetImageSize(0, imageWidth, imageHeight);
			if (imageWidth > 0 && imageHeight > 0)
				displayHeight *= (float)imageHeight / (float)imageWidth;

			// Share the icon's final scale, including map zoom and its minimum-size clamp.
			float labelScale = displayWidth / BASE_ICON_SIZE;
			int fontSize = Math.Max(1, Math.Round(LABEL_FONT_SIZE * labelScale));
			float labelWidth = LABEL_WIDTH * labelScale;
			float labelHeight = Math.Max(LABEL_HEIGHT * labelScale, fontSize);
			float iconOffsetX = (labelWidth - displayWidth) * 0.5;

			vector worldPosition = marker.GetWorldPosition();
			int screenX;
			int screenY;
			m_MapEntity.WorldToScreen(worldPosition[0], worldPosition[2], screenX, screenY, true);
			float localX = workspace.DPIUnscale(screenX) - labelWidth * 0.5;
			float localY = workspace.DPIUnscale(screenY) - displayHeight * 0.5;

			FrameSlot.SetPos(markerRoot, localX, localY);
			// Panning only translates the marker; do not rebuild rotated text geometry.
			if (!updateLayout)
				continue;

			markerDescription.SetExactFontSize(fontSize);
			FrameSlot.SetSize(markerRoot, labelWidth, displayHeight + labelHeight);
			FrameSlot.SetPos(markerGlow, iconOffsetX, 0);
			FrameSlot.SetSize(markerGlow, displayWidth, displayHeight);
			FrameSlot.SetPos(markerImage, iconOffsetX, 0);
			FrameSlot.SetSize(markerImage, displayWidth, displayHeight);
			float textRotation;
			if (marker.IsTextAlignedWithMarkerRotation())
				textRotation = marker.GetRotation();
			float textAngle = textRotation * Math.DEG2RAD;
			// Rotate the label's top-center around the icon center, then rotate
			// its text around that same attachment point (including upside down).
			float labelX = -Math.Sin(textAngle) * displayHeight * 0.5;
			float labelY = (1 + Math.Cos(textAngle)) * displayHeight * 0.5;
			markerDescription.SetPivot(0.5, 0);
			markerDescription.SetRotation(textRotation);
			FrameSlot.SetPos(markerLabel, labelX, labelY);
			FrameSlot.SetSize(markerLabel, labelWidth, labelHeight);
			FrameSlot.SetPos(markerDescription, 0, 0);
			FrameSlot.SetSize(markerDescription, labelWidth, labelHeight);
		}
	}

	//! Project size directly from native pixels-per-meter zoom. Subtracting two
	//! integer screen positions makes size depend on pan rounding.
	protected vector GetProjectedWorldSize(GC_BriefingMarker marker, WorkspaceWidget workspace)
	{
		float size = workspace.DPIUnscale(marker.GetSize() * m_MapEntity.GetCurrentZoom());
		return {size, size, 0};
	}

	protected void ClearMarkers()
	{
		foreach (Widget markerRoot : m_aMarkerRoots)
		{
			if (markerRoot)
			{
				GC_BriefingMarkerWidgetComponent handler = GC_BriefingMarkerWidgetComponent.Cast(markerRoot.FindHandler(GC_BriefingMarkerWidgetComponent));
				if (handler)
					handler.StopPreview();
				markerRoot.RemoveFromHierarchy();
			}
		}
		m_aMarkerRoots.Clear();
		m_aMarkerImages.Clear();
		m_aMarkerGlows.Clear();
		m_aMarkerLabels.Clear();
		m_aMarkerDescriptions.Clear();
		m_aMarkers.Clear();
	}
}
