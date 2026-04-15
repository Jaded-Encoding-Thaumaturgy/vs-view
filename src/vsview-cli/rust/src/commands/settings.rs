use clap::{Args, Subcommand};
use color_print::cstr;

#[derive(Debug, Subcommand, PartialEq, Eq)]
pub(crate) enum Command {
    #[command(
        about = cstr!(
            "Print to stdout the resolved global_settings.json path and exit. \
            The resolved path respects environment scoping if <bold><cyan>--settings-env</cyan></bold> is active. \n\
            Default base directory is: \n\
            - <green>%LOCALAPPDATA%\\vsview\\</green> on Windows, \n\
            - <green>~/.config/vsview/</green> on Linux \n\
            - <green>~/Library/Application Support/vsview/</green> on macOS."
        ),
    )]
    Path,
    #[command(
        about = cstr!(
            "Delete the <bold>global_settings.json</bold> file \
            (as shown by <bold><cyan>vsview settings path</cyan></bold>) and exit."
        ),
    )]
    Wipe(WipeArgs),
}

#[derive(Debug, Args, PartialEq, Eq)]
pub(crate) struct WipeArgs {
    /// Delete the entire settings directory
    /// (including all environment-scoped subdirectories) and exit.
    #[arg(long)]
    pub all: bool,
}
