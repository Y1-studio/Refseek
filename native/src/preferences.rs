use crate::util::*;
use eframe::egui::{self, Color32, FontId, TextStyle, Vec2};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct GuiSettings {
    pub scale: f32,
    pub theme: String,
    pub text_size: f32,
    pub compact: bool,
    pub card_width: f32,
    pub thumbnail_height: f32,
    pub results_per_page: usize,
    pub show_scores: bool,
    pub show_paths: bool,
}
impl Default for GuiSettings {
    fn default() -> Self {
        Self {
            scale: 1.,
            theme: "dark".into(),
            text_size: 15.,
            compact: false,
            card_width: 240.,
            thumbnail_height: 150.,
            results_per_page: 24,
            show_scores: true,
            show_paths: false,
        }
    }
}
impl GuiSettings {
    pub fn sanitize(&mut self) {
        fn bound(x: f32, default: f32, low: f32, high: f32) -> f32 {
            if x.is_finite() {
                x.clamp(low, high)
            } else {
                default
            }
        }
        self.scale = bound(self.scale, 1., 0.75, 2.);
        self.text_size = bound(self.text_size, 15., 12., 22.);
        self.card_width = bound(self.card_width, 240., 200., 400.);
        self.thumbnail_height = bound(self.thumbnail_height, 150., 100., 280.);
        if !["dark", "light", "system"].contains(&self.theme.as_str()) {
            self.theme = "dark".into();
        }
        if ![12, 24, 48, 96].contains(&self.results_per_page) {
            self.results_per_page = 24;
        }
    }
    pub fn load(path: &Path) -> Self {
        let all: serde_json::Value = read_json(path).unwrap_or_default();
        let mut p: Self = serde_json::from_value(all["gui"].clone()).unwrap_or_default();
        p.sanitize();
        p
    }
    pub fn save(&self, path: &Path) -> Res<()> {
        merge(path, "gui", serde_json::to_value(self).map_err(err)?)
    }
    pub fn apply(&self, ctx: &egui::Context) {
        ctx.set_zoom_factor(self.scale);
        // Install both themes so following a Windows theme change stays coherent.
        for theme in [egui::Theme::Dark, egui::Theme::Light] {
            let dark = theme == egui::Theme::Dark;
            let mut style = egui::Style::default();
            style.wrap_mode = Some(egui::TextWrapMode::Wrap);
            style.visuals = if dark {
                egui::Visuals::dark()
            } else {
                egui::Visuals::light()
            };
            if dark {
                style.visuals.panel_fill = Color32::from_rgb(17, 22, 31);
                style.visuals.window_fill = Color32::from_rgb(23, 30, 41);
                style.visuals.extreme_bg_color = Color32::from_rgb(12, 17, 25);
                style.visuals.selection.bg_fill = Color32::from_rgb(38, 91, 143);
                style.visuals.widgets.active.bg_fill = Color32::from_rgb(45, 111, 165);
            }
            style.spacing.item_spacing = if self.compact {
                Vec2::new(7., 5.)
            } else {
                Vec2::new(10., 9.)
            };
            style.spacing.button_padding = if self.compact {
                Vec2::new(9., 5.)
            } else {
                Vec2::new(12., 8.)
            };
            style
                .text_styles
                .insert(TextStyle::Body, FontId::proportional(self.text_size));
            style
                .text_styles
                .insert(TextStyle::Button, FontId::proportional(self.text_size));
            style.text_styles.insert(
                TextStyle::Small,
                FontId::proportional((self.text_size - 3.).max(10.)),
            );
            style.text_styles.insert(
                TextStyle::Heading,
                FontId::proportional(self.text_size + 5.),
            );
            style
                .text_styles
                .insert(TextStyle::Monospace, FontId::monospace(self.text_size - 1.));
            ctx.set_style_of(theme, style);
        }
        ctx.set_theme(match self.theme.as_str() {
            "light" => egui::ThemePreference::Light,
            "system" => egui::ThemePreference::System,
            _ => egui::ThemePreference::Dark,
        });
        // Handle shortcuts ourselves so the saved value and slider always agree.
        ctx.options_mut(|o| o.zoom_with_keyboard = false);
    }
}
pub fn merge(path: &Path, key: &str, value: serde_json::Value) -> Res<()> {
    let mut all: serde_json::Value = if path.exists() {
        read_json(path)?
    } else {
        serde_json::json!({})
    };
    let object = all
        .as_object_mut()
        .ok_or("Invalid settings file: expected an object")?;
    object.insert(key.into(), value);
    save_json(path, &all)
}
