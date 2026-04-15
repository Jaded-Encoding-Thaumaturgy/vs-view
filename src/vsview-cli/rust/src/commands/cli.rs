use std::collections::HashMap;
use std::path::PathBuf;

use clap::builder::styling::Styles;
use clap::{ArgAction, Args, Parser, Subcommand};
use color_print::cstr;
use pyo3::{PyResult, Python};
use pyo3::exceptions::{PySystemExit, PyValueError};
use pyo3::types::{PyAnyMethods, PyString};

pub(crate) const CLAP_STYLING: Styles = Styles::styled()
    .header(clap_cargo::style::HEADER)
    .usage(clap_cargo::style::USAGE)
    .literal(clap_cargo::style::LITERAL)
    .placeholder(clap_cargo::style::PLACEHOLDER)
    .error(clap_cargo::style::ERROR)
    .valid(clap_cargo::style::VALID)
    .invalid(clap_cargo::style::INVALID);

#[derive(Parser, Debug)]
#[command(
    name = "vsview",
    version,
    about = 
        "Preview VapourSynth scripts, videos, images and audio in a desktop viewer.\n\
        Open one or more input files directly, or start without files to open the default workspaces"
    ,
    styles = CLAP_STYLING
)]
pub(crate) struct Cli {
    /// Path to input file(s); video(s), image(s) or script(s).
    #[arg(value_name = "FILE")]
    pub files: Vec<PathBuf>,

    /// Enable verbose output. Repeat to increase verbosity (-v, -vv, -vvv, ...).
    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// Argument passed to the script environment. Can be specified multiple times.
    #[arg(short, long, value_name = "KEY=VALUE", value_parser = parse_script_arg)]
    pub arg: Vec<ScriptArg>,

    /// Pass an argument directly to the underlying Qt application.
    #[arg(short = 'q', long, value_name = "ARG")]
    pub qt_arg: Vec<String>,

    #[command(flatten)]
    pub settings_config: SettingsArgs,

    #[command(subcommand)]
    pub command: Option<Commands>,
}

#[derive(Debug, Args)]
#[allow(clippy::struct_excessive_bools)]
#[command(next_help_heading = "Settings Options")]
pub(crate) struct SettingsArgs {
    /// Run without loading or saving any settings for this session.
    #[arg(long, env = "VSVIEW_NO_SETTINGS")]
    pub no_settings: bool,

    #[cfg(windows)]
    #[arg(
        long,
        env = "VSVIEW_GLOBAL_SETTINGS_ROAMING",
        help = cstr!(
            "Store global settings in <green>%APPDATA%</green> \
            instead of <green>%LOCALAPPDATA%</green> (Windows only)."),
    )]
    pub settings_roaming: bool,

    /// Scope global settings to the active Python environment to prevent conflicts.
    #[arg(long, env = "VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT")]
    pub settings_env: bool,

    #[arg(
        long,
        env = "VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY",
        help = cstr!(
            "If <bold>--settings-env</bold> is set, and the scoped file doesn't exist yet, \
            seed it from the base <bold>global_settings.json</bold>."
        ),
    )]
    pub settings_env_copy: bool,
}

#[derive(Debug, Subcommand)]
pub(crate) enum Commands {
    /// Settings management.
    Settings {
        #[command(subcommand)]
        command: super::settings::Command,
    },
    /// Show the installed vsview version and exit.
    Version,
}


#[derive(Debug, Clone)]
pub(crate) struct ScriptArg {
    pub key: String,
    pub value: String,
}

fn parse_script_arg(item: &str) -> Result<ScriptArg, String> {
    let (key, value) = item
        .split_once('=')
        .ok_or_else(|| format!("No value specified for argument {item}"))?;

    // Use Python's own logic to validate the key
    let res = Python::try_attach(|py| validate_arg_key(py, key));

    match res {
        Some(Ok(())) => Ok(()),
        Some(Err(e)) => Err(e.to_string()),
        None => Err("Python interpreter not initialized".to_string()),
    }?;

    Ok(ScriptArg {
        key: key.to_string(),
        value: value.to_string(),
    })
}

fn validate_arg_key(py: Python<'_>, key: &str) -> PyResult<()> {
    let key_py = PyString::new(py, key);

    let is_ident: bool = key_py.call_method0("isidentifier")?.extract()?;

    if !is_ident {
        return Err(PyValueError::new_err(format!(
            "Invalid argument name {key:?} (must be a valid Python identifier)"
        )));
    }

    let is_kw = py.import("keyword")?.call_method1("iskeyword", (key_py,))?.extract()?;

    if is_kw {
        return Err(PyValueError::new_err(format!(
            "Invalid argument name {key:?} (is a reserved Python keyword)"
        )));
    }
    Ok(())
}

/// Convert a list of ``ScriptArg`` to a ``HashMap`` (like python dict)
pub(crate) fn script_args_to_map(items: Vec<ScriptArg>) -> HashMap<String, String> {
    // This could probably be inlined where it's called but whatever
    items.into_iter().map(|item| (item.key, item.value)).collect()
}

pub(crate) fn show_version(py: Python<'_>) -> PyResult<()> {
    let metadata = py.import("importlib.metadata")?;
    let version_r = metadata.call_method1("version", ("vsview",));

    let version: String = match version_r {
        Ok(v) => v.extract()?,
        Err(e) => {
            let pkg_not_found_err = metadata.getattr("PackageNotFoundError")?;
            if e.is_instance(py, &pkg_not_found_err) {
                "unknown".to_string()
            } else {
                return Err(e);
            }
        }
    };
    println!("vsview {version}");
    Err(PySystemExit::new_err(0))
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn verify_cli() {
        Cli::command().debug_assert();
    }
}
