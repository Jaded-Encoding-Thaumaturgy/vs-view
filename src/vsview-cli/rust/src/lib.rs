mod commands;

use clap::{CommandFactory, Error, FromArgMatches};
use pyo3::exceptions::PySystemExit;
use pyo3::prelude::{
    Bound, PyDictMethods, PyErr, PyModule, PyModuleMethods, PyResult, Python, pyfunction, pymodule, wrap_pyfunction,
};
use pyo3::types::PyDict;
use pyo3_stub_gen::define_stub_info_gatherer;
use pyo3_stub_gen::derive::gen_stub_pyfunction;

use crate::commands::cli::{Cli, Commands, script_args_to_map, show_version};
use crate::commands::settings;

define_stub_info_gatherer!(gather_stub_info);

#[gen_stub_pyfunction(python = r#"
    import collections.abc
    import typing

    def parse_args(args: collections.abc.Sequence[str], columns: int | None = None) -> dict[str, typing.Any]: ...
"#)]
#[pyfunction]
#[pyo3(signature = (args, columns = None))]
fn parse_args(py: Python<'_>, args: Vec<String>, columns: Option<usize>) -> PyResult<Bound<'_, PyDict>> {
    let mut cmd = Cli::command();
    // Set manually the maximum width of the terminal
    // Since the main process is started from Python, clap doesn't know the width from here.
    if let Some(w) = columns {
        cmd = cmd.term_width(w);
    }

    // Try to parse the strings in args
    // If clap exits (help/version/usage error), print and propagate the exit code to Python.
    let matches = cmd.try_get_matches_from(args).map_err(|e| clap_exit(&e))?;
    // The actual parsing
    let cli = Cli::from_arg_matches(&matches).map_err(|e| clap_exit(&e))?;

    let dict = PyDict::new(py);
    let mut settings_dict = None;

    if let Some(command) = cli.command {
        // Reminder: this dict only shadows the upper dict in this scope.
        let dict = PyDict::new(py);

        match &command {
            Commands::Settings { command: s_cmd } => match s_cmd {
                settings::Command::Path => {
                    dict.set_item("path", true)?;
                }
                settings::Command::Wipe(wipe_args) => {
                    let wipe_dict = PyDict::new(py);
                    wipe_dict.set_item("all", wipe_args.all)?;
                    dict.set_item("wipe", wipe_dict)?;
                }
            },
            Commands::Version => {
                show_version(py)?;
                unreachable!()
            }
        }
        settings_dict = Some(dict);
    }

    dict.set_item("files", cli.files)?;
    dict.set_item("settings", settings_dict)?;
    dict.set_item("no_settings", cli.settings_config.no_settings)?;
    dict.set_item(
        "settings_roaming",
        #[cfg(windows)]
        cli.settings_config.settings_roaming,
        #[cfg(not(windows))]
        false,
    )?;
    dict.set_item("settings_env", cli.settings_config.settings_env)?;
    dict.set_item("settings_env_copy", cli.settings_config.settings_env_copy)?;
    dict.set_item("verbose", cli.verbose)?;
    dict.set_item("arg", script_args_to_map(cli.arg))?;
    dict.set_item("qt_arg", cli.qt_arg)?;

    Ok(dict)
}

fn clap_exit(error: &Error) -> PyErr {
    let exit_code = error.exit_code();
    let _ = error.print();

    PySystemExit::new_err(exit_code)
}

#[pymodule]
#[pyo3(name = "_cli")]
fn _cli(pymodule: &Bound<'_, PyModule>) -> PyResult<()> {
    pymodule.add_function(wrap_pyfunction!(parse_args, pymodule)?)?;
    Ok(())
}
