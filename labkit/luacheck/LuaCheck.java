import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;

/**
 * Syntax-checks Lua files with the game's own Kahlua compiler, so the result matches what
 * Project Zomboid will accept. The compiler is reached by reflection: this file compiles
 * without the game jar, and the game jar (Java 25 bytecode) is only needed at run time.
 *
 * Usage: java -cp <build dir>;<projectzomboid.jar> LuaCheck <file or dir>...
 */
public class LuaCheck {
    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            System.err.println("usage: LuaCheck <file or dir>...");
            System.exit(2);
        }

        Class<?> compiler = Class.forName("se.krka.kahlua.luaj.compiler.LuaCompiler");
        Class<?> table = Class.forName("se.krka.kahlua.vm.KahluaTable");
        Method loadstring = compiler.getMethod("loadstring", String.class, String.class, table);

        List<Path> files = new ArrayList<>();
        for (String arg : args) {
            collect(Paths.get(arg), files);
        }

        int failures = 0;
        for (Path file : files) {
            String source = Files.readString(file, StandardCharsets.UTF_8);
            if (source.startsWith("﻿")) {
                source = source.substring(1);
            }
            try {
                loadstring.invoke(null, source, file.toString(), null);
            } catch (InvocationTargetException e) {
                failures++;
                Throwable cause = e.getCause() != null ? e.getCause() : e;
                System.out.println("FAIL " + file + ": " + cause.getMessage());
            }
        }

        System.out.println((files.size() - failures) + "/" + files.size() + " Lua files OK");
        System.exit(failures == 0 ? 0 : 1);
    }

    private static void collect(Path path, List<Path> out) throws IOException {
        if (Files.isDirectory(path)) {
            try (Stream<Path> walk = Files.walk(path)) {
                walk.filter(p -> p.toString().endsWith(".lua")).sorted().forEach(out::add);
            }
        } else {
            out.add(path);
        }
    }
}
