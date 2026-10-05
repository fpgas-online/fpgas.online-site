# The catalogue names a board by its USB serial and no longer says where it is plugged in, whether it is
# "enabled" or which Commander drives it: the fleet's boot checks say what is connected (ttsite/boards.py).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ttsite", "0003_board_commander"),
    ]

    operations = [
        migrations.RemoveField(model_name="board", name="switch"),
        migrations.RemoveField(model_name="board", name="port"),
        migrations.RemoveField(model_name="board", name="enabled"),
        migrations.RemoveField(model_name="board", name="commander"),
        migrations.AddField(
            model_name="board",
            name="usb_serial",
            field=models.CharField(
                blank=True,
                help_text="the board's USB serial, as on its label; empty = not here yet",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="board",
            name="kind",
            field=models.CharField(
                choices=[("asic", "TT ASIC"), ("kianv", "KianV RISC-V"), ("fpga", "FPGA emulation")],
                help_text="where the index files this row while no boot check reports the board",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="board",
            name="shuttle",
            field=models.CharField(
                blank=True,
                help_text="e.g. tt06, for a row no boot check reports; a reported board says its own",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="board",
            constraint=models.UniqueConstraint(
                condition=models.Q(("usb_serial", ""), _negated=True),
                fields=("usb_serial",),
                name="ttsite_one_row_per_usb_serial",
            ),
        ),
    ]
