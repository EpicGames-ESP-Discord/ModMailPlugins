import asyncio
import io
import logging
from typing import List, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from core import checks
from core.models import PermissionLevel

logger = logging.getLogger("Modmail")

HAS_FILE_UPLOAD = hasattr(discord.ui, "FileUpload") and hasattr(discord.ui, "Label")
MAX_FILES = 10
MAX_FILE_BYTES = 8 * 1024 * 1024


class ReportModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "Report",
        target: Union[discord.Member, discord.User, discord.Message],
    ):
        self.is_message = isinstance(target, discord.Message)
        super().__init__(title="Reportar mensaje" if self.is_message else "Reportar usuario")
        self.cog = cog
        self.target = target

        self.reason = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Describe el motivo del reporte...",
            min_length=5,
            max_length=1000,
            required=True,
        )
        self.files = None
        self.links = None

        if HAS_FILE_UPLOAD:
            self.add_item(discord.ui.Label(text="Razón", component=self.reason))
            # Adjuntos obligatorios solo al reportar usuarios
            self.files = discord.ui.FileUpload(
                required=not self.is_message, min_values=0 if self.is_message else 1, max_values=MAX_FILES
            )
            self.add_item(
                discord.ui.Label(
                    text="Adjuntos (evidencia)",
                    description="Opcional" if self.is_message else "Obligatorio",
                    component=self.files,
                )
            )
        else:
            self.links = discord.ui.TextInput(
                style=discord.TextStyle.paragraph,
                required=not self.is_message,
                max_length=1000,
            )
            if hasattr(discord.ui, "Label"):
                self.add_item(discord.ui.Label(text="Razón", component=self.reason))
                self.add_item(discord.ui.Label(text="Enlaces de evidencia", component=self.links))
            else:
                self.reason.label = "Razón"
                self.links.label = "Enlaces de evidencia"
                self.add_item(self.reason)
                self.add_item(self.links)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        attachments: List[discord.Attachment] = list(self.files.values) if self.files else []
        links = self.links.value.strip() if self.links else ""
        try:
            thread = await self.cog.create_report(
                interaction.user, self.target, self.reason.value, attachments, links
            )
        except Exception:
            logger.exception("Error al crear el reporte")
            return await interaction.followup.send(
                "❌ No se pudo crear el reporte. Inténtalo más tarde.", ephemeral=True
            )
        if thread is None:
            return await interaction.followup.send(
                "❌ No puedes crear reportes en este momento.", ephemeral=True
            )
        await interaction.followup.send("✅ Tu reporte fue enviado al equipo de moderación.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error("Error en ReportModal", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Ocurrió un error.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Ocurrió un error.", ephemeral=True)


class Report(commands.Cog, name="ReportThreads"):
    """Reporta usuarios o mensajes mediante el menú contextual; crea un thread de Modmail."""

    def __init__(self, bot):
        self.bot = bot
        self.menus = [
            app_commands.ContextMenu(name="Reportar usuario", callback=self.report_user),
            app_commands.ContextMenu(name="Reportar mensaje", callback=self.report_message),
        ]
        self._sync_task: Optional[asyncio.Task] = None

    def _guild_obj(self) -> Optional[discord.Object]:
        gid = getattr(self.bot, "guild_id", None)
        return discord.Object(id=gid) if gid else None

    async def cog_load(self):
        guild = self._guild_obj()
        for menu in self.menus:
            self.bot.tree.add_command(menu, guild=guild, override=True)
        self._sync_task = asyncio.create_task(self._sync_safe())

    async def _sync_safe(self):
        try:
            await self._sync()
        except Exception:
            pass  # ya registrado en el log por _sync

    async def cog_unload(self):
        guild = self._guild_obj()
        for menu in self.menus:
            self.bot.tree.remove_command(menu.name, guild=guild, type=menu.type)
        if self._sync_task:
            self._sync_task.cancel()
        try:
            await self.bot.tree.sync(guild=guild)
        except Exception:
            logger.exception("No se pudo sincronizar los comandos al descargar el plugin de reportes")

    async def _sync(self) -> List[str]:
        """Sincroniza los menús en el servidor y elimina copias globales de versiones anteriores."""
        await self.bot.wait_until_ready()
        guild = self._guild_obj()
        try:
            synced = await self.bot.tree.sync(guild=guild)
            if guild:
                await self.bot.tree.sync()
            names = [c.name for c in synced]
            logger.info("Menús contextuales sincronizados: %s", ", ".join(names) or "ninguno")
            return names
        except Exception:
            logger.exception("No se pudo sincronizar los menús contextuales de reportes")
            raise

    @commands.command()
    @checks.has_permissions(PermissionLevel.OWNER)
    async def reportsync(self, ctx):
        """Fuerza la sincronización de los menús contextuales de reportes."""
        try:
            names = await self._sync()
        except Exception as e:
            return await ctx.send(f"❌ Error al sincronizar: `{e}`")
        await ctx.send(f"✅ Comandos sincronizados: {', '.join(f'`{n}`' for n in names) or 'ninguno'}")

    async def _precheck(self, interaction: discord.Interaction, target_user) -> bool:
        if target_user.id == interaction.user.id:
            await interaction.response.send_message("No puedes reportarte a ti mismo.", ephemeral=True)
            return False
        if target_user.bot:
            await interaction.response.send_message("No puedes reportar a un bot.", ephemeral=True)
            return False
        return True

    async def report_user(self, interaction: discord.Interaction, user: discord.Member):
        if await self._precheck(interaction, user):
            await interaction.response.send_modal(ReportModal(self, user))

    async def report_message(self, interaction: discord.Interaction, message: discord.Message):
        if await self._precheck(interaction, message.author):
            await interaction.response.send_modal(ReportModal(self, message))

    def _build_embed(self, reporter, target, reason: str, links: str, include_content: bool = True) -> discord.Embed:
        embed = discord.Embed(title="🚩 Nuevo reporte", description=reason, color=discord.Color.red())
        embed.timestamp = discord.utils.utcnow()
        embed.add_field(name="Reportado por", value=f"{reporter.mention} (`{reporter.id}`)", inline=False)

        if isinstance(target, discord.Message):
            author = target.author
            embed.add_field(name="Tipo", value="Mensaje", inline=True)
            embed.add_field(name="Autor del mensaje", value=f"{author.mention} (`{author.id}`)", inline=True)
            embed.add_field(name="Canal", value=target.channel.mention, inline=True)
            embed.add_field(name="Enlace", value=f"[Ir al mensaje]({target.jump_url})", inline=True)
            if include_content:
                content = target.content or "*Sin contenido de texto*"
                if len(content) > 1000:
                    content = content[:997] + "..."
                embed.add_field(name="Contenido", value=content, inline=False)
                if target.attachments:
                    embed.add_field(
                        name="Adjuntos del mensaje",
                        value="\n".join(a.url for a in target.attachments)[:1000],
                        inline=False,
                    )
            embed.set_thumbnail(url=author.display_avatar.url)
        else:
            embed.add_field(name="Tipo", value="Usuario", inline=True)
            embed.add_field(name="Usuario reportado", value=f"{target.mention} (`{target.id}`)", inline=True)
            embed.set_thumbnail(url=target.display_avatar.url)

        if links:
            embed.add_field(name="Enlaces de evidencia", value=links[:1000], inline=False)
        return embed

    async def _files(self, attachments: List[discord.Attachment]) -> List[discord.File]:
        files = []
        for att in attachments[:MAX_FILES]:
            if att.size > MAX_FILE_BYTES:
                continue
            try:
                files.append(discord.File(io.BytesIO(await att.read()), filename=att.filename))
            except discord.HTTPException:
                logger.warning("No se pudo descargar el adjunto %s", att.filename)
        return files

    async def create_report(self, reporter, target, reason: str, attachments, links: str):
        """Crea (o reutiliza) el thread del reportante y publica el reporte. Devuelve el thread."""
        bot = self.bot
        if await bot.is_blocked(reporter):
            return None

        thread = await bot.threads.find(recipient=reporter)
        if thread is None:
            thread = await bot.threads.create(reporter, creator=reporter, manual_trigger=False)
        await thread.wait_until_ready()
        if thread.channel is None:
            raise RuntimeError("El canal del thread no está disponible")

        files = await self._files(attachments)
        if isinstance(target, discord.Message):
            await thread.channel.send(embed=self._build_embed(reporter, target, reason, links, False), files=files)
            try:
                await target.forward(thread.channel)
            except discord.HTTPException:
                logger.warning("No se pudo reenviar el mensaje reportado; se usa el contenido en un embed")
                await thread.channel.send(embed=self._build_embed(reporter, target, reason, links))
        else:
            await thread.channel.send(embed=self._build_embed(reporter, target, reason, links), files=files)
        return thread


async def setup(bot):
    await bot.add_cog(Report(bot))
